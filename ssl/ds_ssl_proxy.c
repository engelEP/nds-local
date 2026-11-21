/* Decrypt the console's SSLv3 connection and forward the HTTP to this machine.

   Build this against OpenSSL 1.0.2 (ssl/build_ds_ssl.sh). The console's
   ClientHello uses the SSLv2 record format, which OpenSSL 1.1 and later refuse.
   The proxy speaks only that old handshake and only to the local HTTP port.
*/

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include <openssl/crypto.h>
#include <openssl/err.h>
#include <openssl/ssl.h>

#define REQUEST_CAP (80 * 1024)
/* A served file is at most 2 MiB. The extra room is the HTTP header. */
#define RESPONSE_CAP ((2 * 1024 * 1024) + (64 * 1024))

typedef struct Config {
    const char *bind_host;
    int port;
    const char *backend_host;
    int backend_port;
    const char *cert;
    const char *key;
} Config;

typedef struct Conn {
    int client;
    SSL_CTX *ctx;
    const char *backend_host;
    int backend_port;
} Conn;

#if OPENSSL_VERSION_NUMBER < 0x10100000L
/* 1.0.2 shares the error queue and SSL_CTX across threads only when these are set. */
static pthread_mutex_t *ssl_locks;
static int ssl_lock_count;

static unsigned long ssl_thread_id(void) {
    return (unsigned long)pthread_self();
}

static void ssl_lock_callback(int mode, int index, const char *file, int line) {
    (void)file;
    (void)line;
    if (index < 0 || index >= ssl_lock_count) {
        return;
    }
    if (mode & CRYPTO_LOCK) {
        pthread_mutex_lock(&ssl_locks[index]);
    } else {
        pthread_mutex_unlock(&ssl_locks[index]);
    }
}
#endif

static int ssl_threads_setup(void) {
#if OPENSSL_VERSION_NUMBER < 0x10100000L
    int count = CRYPTO_num_locks();
    if (count <= 0) {
        return 1;
    }
    ssl_locks = calloc((size_t)count, sizeof(*ssl_locks));
    if (ssl_locks == NULL) {
        fprintf(stderr, "Cannot allocate OpenSSL thread locks\n");
        return 0;
    }
    for (int i = 0; i < count; i++) {
        if (pthread_mutex_init(&ssl_locks[i], NULL) != 0) {
            fprintf(stderr, "Cannot create OpenSSL thread locks\n");
            ssl_lock_count = i;
            return 0;
        }
    }
    ssl_lock_count = count;
    CRYPTO_set_id_callback(ssl_thread_id);
    CRYPTO_set_locking_callback(ssl_lock_callback);
#endif
    return 1;
}

static void ssl_threads_cleanup(void) {
#if OPENSSL_VERSION_NUMBER < 0x10100000L
    CRYPTO_set_locking_callback(NULL);
    CRYPTO_set_id_callback(NULL);
    if (ssl_locks == NULL) {
        return;
    }
    for (int i = 0; i < ssl_lock_count; i++) {
        pthread_mutex_destroy(&ssl_locks[i]);
    }
    free(ssl_locks);
    ssl_locks = NULL;
    ssl_lock_count = 0;
#endif
}

static void usage(void) {
    fprintf(stderr,
            "usage: ds_ssl_proxy --bind HOST --port PORT --backend-host HOST "
            "--backend-port PORT --cert FILE --key FILE\n");
}

static int parse_port(const char *text) {
    char *end = NULL;
    long value = strtol(text, &end, 10);
    if (end == text || *end != '\0' || value < 1 || value > 65535) {
        return -1;
    }
    return (int)value;
}

static int take_option(int argc, char **argv, int *index, const char *name, const char **dest) {
    if (strcmp(argv[*index], name) != 0) {
        return 0;
    }
    if (*index + 1 >= argc) {
        fprintf(stderr, "missing value for %s\n", name);
        return -1;
    }
    *dest = argv[*index + 1];
    *index += 1;
    return 1;
}

static int parse_args(int argc, char **argv, Config *config) {
    const char *port_text = NULL;
    const char *backend_port_text = NULL;
    memset(config, 0, sizeof(*config));
    for (int i = 1; i < argc; i++) {
        int taken = take_option(argc, argv, &i, "--bind", &config->bind_host);
        if (taken == 0) {
            taken = take_option(argc, argv, &i, "--port", &port_text);
        }
        if (taken == 0) {
            taken = take_option(argc, argv, &i, "--backend-host", &config->backend_host);
        }
        if (taken == 0) {
            taken = take_option(argc, argv, &i, "--backend-port", &backend_port_text);
        }
        if (taken == 0) {
            taken = take_option(argc, argv, &i, "--cert", &config->cert);
        }
        if (taken == 0) {
            taken = take_option(argc, argv, &i, "--key", &config->key);
        }
        if (taken < 0) {
            return 0;
        }
        if (taken == 0) {
            fprintf(stderr, "unknown argument %s\n", argv[i]);
            return 0;
        }
    }
    if (config->bind_host == NULL || config->backend_host == NULL || config->cert == NULL ||
        config->key == NULL || port_text == NULL || backend_port_text == NULL) {
        usage();
        return 0;
    }
    config->port = parse_port(port_text);
    config->backend_port = parse_port(backend_port_text);
    if (config->port < 0 || config->backend_port < 0) {
        fprintf(stderr, "port must be an integer from 1 to 65535\n");
        return 0;
    }
    return 1;
}

static int header_end(const unsigned char *buf, size_t len) {
    if (len < 4) {
        return -1;
    }
    for (size_t i = 0; i + 3 < len; i++) {
        if (buf[i] == '\r' && buf[i + 1] == '\n' && buf[i + 2] == '\r' && buf[i + 3] == '\n') {
            return (int)(i + 4);
        }
    }
    return -1;
}

static int starts_with_token(const unsigned char *buf, size_t len, const char *token) {
    size_t n = strlen(token);
    if (len < n) {
        return 0;
    }
    for (size_t i = 0; i < n; i++) {
        if (tolower(buf[i]) != token[i]) {
            return 0;
        }
    }
    return 1;
}

/* Body size from the headers. 0 when the header is absent. -1 when it is unusable. */
static long content_length_of(const unsigned char *buf, size_t header_len) {
    const char token[] = "content-length:";
    size_t token_len = sizeof(token) - 1;
    for (size_t i = 0; i + token_len < header_len; i++) {
        if (buf[i] != '\n' && i != 0) {
            continue;
        }
        size_t start = i == 0 ? 0 : i + 1;
        if (!starts_with_token(buf + start, header_len - start, token)) {
            continue;
        }
        start += token_len;
        while (start < header_len && (buf[start] == ' ' || buf[start] == '\t')) {
            start++;
        }
        if (start >= header_len || !isdigit(buf[start])) {
            return -1;
        }
        long value = 0;
        while (start < header_len && isdigit(buf[start])) {
            if (value > (REQUEST_CAP / 10)) {
                return -1;
            }
            value = value * 10 + (buf[start] - '0');
            start++;
        }
        return value;
    }
    return 0;
}

/* 1 when the buffer holds one full request, 0 when more bytes are required, -1 when it is invalid. */
static int request_ready(const unsigned char *buf, size_t len, size_t *total) {
    int headers = header_end(buf, len);
    if (headers < 0) {
        return 0;
    }
    long body = content_length_of(buf, (size_t)headers);
    if (body < 0) {
        return -1;
    }
    if ((size_t)headers > REQUEST_CAP || (size_t)body > REQUEST_CAP - (size_t)headers) {
        return -1;
    }
    *total = (size_t)headers + (size_t)body;
    return len >= *total ? 1 : 0;
}

static void log_request_line(const unsigned char *buf, size_t len) {
    size_t n = 0;
    while (n < len && n < 160 && buf[n] != '\r' && buf[n] != '\n') {
        n++;
    }
    fprintf(stderr, "SSL %.*s\n", (int)n, buf);
    fflush(stderr);
}

static int ssl_write_all(SSL *ssl, const unsigned char *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        int n = SSL_write(ssl, buf + sent, (int)(len - sent));
        if (n <= 0) {
            return 0;
        }
        sent += (size_t)n;
    }
    return 1;
}

static int send_all(int fd, const unsigned char *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, buf + sent, len - sent, 0);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return 0;
        }
        sent += (size_t)n;
    }
    return 1;
}

static int read_request(SSL *ssl, unsigned char *buf, size_t *len) {
    size_t used = 0;
    size_t total = 0;
    for (;;) {
        int ready = request_ready(buf, used, &total);
        if (ready == 1) {
            *len = total;
            return 1;
        }
        if (ready < 0 || used >= REQUEST_CAP) {
            return 0;
        }
        int n = SSL_read(ssl, buf + used, (int)(REQUEST_CAP - used));
        if (n <= 0) {
            return 0;
        }
        used += (size_t)n;
    }
}

static int read_response(int fd, unsigned char *buf, size_t *len) {
    size_t used = 0;
    for (;;) {
        if (used >= RESPONSE_CAP) {
            unsigned char extra;
            ssize_t n = recv(fd, &extra, 1, MSG_PEEK);
            if (n < 0 && errno == EINTR) {
                continue;
            }
            if (n == 0) {
                *len = used;
                return 1;
            }
            return 0;
        }
        ssize_t n = recv(fd, buf + used, RESPONSE_CAP - used, 0);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0) {
            return 0;
        }
        if (n == 0) {
            *len = used;
            return used > 0;
        }
        used += (size_t)n;
    }
}

static int connect_backend(const char *host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        close(fd);
        return -1;
    }
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void set_timeouts(int fd) {
    struct timeval tv;
    tv.tv_sec = 30;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

static void serve_client(Conn *conn) {
    unsigned char *request = NULL;
    unsigned char *response = NULL;
    SSL *ssl = NULL;
    int backend = -1;
    int nodelay = 1;
    setsockopt(conn->client, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
    set_timeouts(conn->client);

    ssl = SSL_new(conn->ctx);
    if (ssl == NULL || SSL_set_fd(ssl, conn->client) != 1 || SSL_accept(ssl) != 1) {
        fprintf(stderr, "SSL handshake failed\n");
        ERR_print_errors_fp(stderr);
        goto done;
    }
    fprintf(stderr, "SSL handshake %s\n", SSL_get_cipher(ssl));
    fflush(stderr);

    request = malloc(REQUEST_CAP);
    if (request == NULL) {
        goto done;
    }
    size_t request_len = 0;
    if (!read_request(ssl, request, &request_len)) {
        fprintf(stderr, "SSL request was incomplete\n");
        goto done;
    }
    log_request_line(request, request_len);

    backend = connect_backend(conn->backend_host, conn->backend_port);
    if (backend < 0) {
        fprintf(stderr, "SSL backend %s:%d is not accepting\n", conn->backend_host, conn->backend_port);
        goto done;
    }
    set_timeouts(backend);
    if (!send_all(backend, request, request_len)) {
        fprintf(stderr, "SSL backend did not take the request\n");
        goto done;
    }
    response = malloc(RESPONSE_CAP);
    if (response == NULL) {
        goto done;
    }
    size_t response_len = 0;
    if (!read_response(backend, response, &response_len) ||
        !ssl_write_all(ssl, response, response_len)) {
        fprintf(stderr, "SSL response was not delivered\n");
        goto done;
    }

done:
    free(request);
    free(response);
    if (backend >= 0) {
        close(backend);
    }
    if (ssl != NULL) {
        SSL_shutdown(ssl);
        SSL_free(ssl);
    }
    close(conn->client);
    free(conn);
}

static void *serve_thread(void *arg) {
    serve_client(arg);
    return NULL;
}

static SSL_CTX *make_context(const Config *config) {
#if OPENSSL_VERSION_NUMBER < 0x10100000L
    SSL_library_init();
    SSL_load_error_strings();
    OpenSSL_add_all_algorithms();
    SSL_CTX *ctx = SSL_CTX_new(SSLv23_server_method());
#else
    SSL_CTX *ctx = SSL_CTX_new(TLS_server_method());
#endif
    if (ctx == NULL) {
        fprintf(stderr, "OpenSSL refused to create an SSLv3 context\n");
        ERR_print_errors_fp(stderr);
        return NULL;
    }
#if OPENSSL_VERSION_NUMBER < 0x10100000L
    /* TLS is turned off. SSLv2 stays available so the console's old hello is accepted. */
    SSL_CTX_set_options(ctx, SSL_OP_NO_TLSv1 | SSL_OP_NO_TLSv1_1 | SSL_OP_NO_TLSv1_2);
    if (SSL_CTX_set_cipher_list(ctx, "RC4-MD5:RC4-SHA") != 1) {
#else
    SSL_CTX_set_security_level(ctx, 0);
    SSL_CTX_clear_options(ctx, SSL_OP_NO_SSLv2 | SSL_OP_NO_SSLv3);
    if (SSL_CTX_set_min_proto_version(ctx, SSL3_VERSION) != 1 ||
        SSL_CTX_set_max_proto_version(ctx, SSL3_VERSION) != 1 ||
        SSL_CTX_set_cipher_list(ctx, "RC4-MD5:RC4-SHA:@SECLEVEL=0") != 1) {
#endif
        fprintf(stderr,
                "This OpenSSL cannot offer SSLv3 with RC4. Rebuild with ssl/build_ds_ssl.sh\n");
        ERR_print_errors_fp(stderr);
        SSL_CTX_free(ctx);
        return NULL;
    }
    if (SSL_CTX_use_certificate_chain_file(ctx, config->cert) != 1 ||
        SSL_CTX_use_PrivateKey_file(ctx, config->key, SSL_FILETYPE_PEM) != 1 ||
        SSL_CTX_check_private_key(ctx) != 1) {
        fprintf(stderr, "Cannot load certificate %s and key %s\n", config->cert, config->key);
        ERR_print_errors_fp(stderr);
        SSL_CTX_free(ctx);
        return NULL;
    }
    return ctx;
}

static int listen_on(const char *host, int port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((unsigned short)port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        fprintf(stderr, "%s is not an IPv4 address\n", host);
        close(fd);
        return -1;
    }
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        if (errno == EACCES) {
            fprintf(stderr, "Cannot listen on %s:%d for SSL. Ports below 1024 need extra privilege.\n",
                    host, port);
        } else if (errno == EADDRINUSE) {
            fprintf(stderr, "SSL port %d on %s is already in use.\n", port, host);
        } else {
            fprintf(stderr, "Cannot listen on %s:%d for SSL: %s\n", host, port, strerror(errno));
        }
        close(fd);
        return -1;
    }
    if (listen(fd, 16) < 0) {
        fprintf(stderr, "Cannot listen on %s:%d for SSL: %s\n", host, port, strerror(errno));
        close(fd);
        return -1;
    }
    return fd;
}

int main(int argc, char **argv) {
    Config config;
    if (!parse_args(argc, argv, &config)) {
        return 1;
    }
    setvbuf(stderr, NULL, _IONBF, 0);
    signal(SIGPIPE, SIG_IGN);
    if (!ssl_threads_setup()) {
        ssl_threads_cleanup();
        return 1;
    }
    SSL_CTX *ctx = make_context(&config);
    if (ctx == NULL) {
        ssl_threads_cleanup();
        return 1;
    }
    int listener = listen_on(config.bind_host, config.port);
    if (listener < 0) {
        SSL_CTX_free(ctx);
        ssl_threads_cleanup();
        return 1;
    }
#if OPENSSL_VERSION_NUMBER < 0x10100000L
    fprintf(stderr, "SSL proxy %s\n", SSLeay_version(SSLEAY_VERSION));
#else
    fprintf(stderr, "SSL proxy %s\n", OpenSSL_version(OPENSSL_VERSION));
#endif
    fprintf(stderr, "SSL proxy listening on %s:%d\n", config.bind_host, config.port);
    fflush(stderr);

    for (;;) {
        int client = accept(listener, NULL, NULL);
        if (client < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "SSL accept failed: %s\n", strerror(errno));
            break;
        }
        Conn *conn = calloc(1, sizeof(*conn));
        if (conn == NULL) {
            close(client);
            continue;
        }
        conn->client = client;
        conn->ctx = ctx;
        conn->backend_host = config.backend_host;
        conn->backend_port = config.backend_port;
        pthread_t thread;
        if (pthread_create(&thread, NULL, serve_thread, conn) != 0) {
            close(client);
            free(conn);
            continue;
        }
        pthread_detach(thread);
    }
    close(listener);
    SSL_CTX_free(ctx);
    ssl_threads_cleanup();
    return 1;
}
