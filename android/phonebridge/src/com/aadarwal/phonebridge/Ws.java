package com.aadarwal.phonebridge;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Locale;

import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * The smallest WebSocket client that is actually correct.
 *
 * This app ships no dependencies — no OkHttp, no Gradle — and Android's
 * runtime has no java.net.http. Muse's realtime endpoint is a WebSocket, so
 * one is written here: the HTTP upgrade, RFC 6455 framing, client-side
 * masking (mandatory; a server MUST close on an unmasked client frame), and
 * the ping/pong/close handshake. About two hundred lines, which is what the
 * protocol costs when you skip the parts a transcription client never uses
 * (extensions, subprotocol negotiation, fragmented continuation frames from
 * the server — Muse sends whole JSON events).
 *
 * Blocking, and deliberately so. A reader thread in {@link Muse} owns
 * {@link #recv()}; the recording loop owns {@link #send}. Nothing here is
 * shared between them except the socket, whose streams are independent.
 */
final class Ws {

    static final int OP_CONT = 0x0, OP_TEXT = 0x1, OP_BINARY = 0x2,
                     OP_CLOSE = 0x8, OP_PING = 0x9, OP_PONG = 0xA;

    static final class Frame {
        final int opcode;
        final byte[] payload;
        Frame(int opcode, byte[] payload) { this.opcode = opcode; this.payload = payload; }
        String text() { return new String(payload, StandardCharsets.UTF_8); }
    }

    private final Socket sock;
    private final InputStream in;
    private final OutputStream out;
    private final SecureRandom rnd = new SecureRandom();
    private final Object writeLock = new Object();
    private volatile boolean closed = false;

    private Ws(Socket sock) throws IOException {
        this.sock = sock;
        this.in = new BufferedInputStream(sock.getInputStream(), 1 << 16);
        this.out = sock.getOutputStream();
    }

    /**
     * Open wss://host/path. Only TLS, only port 443: that is the one shape
     * this app will ever dial, and a plaintext option is a foot-gun with no
     * caller.
     */
    static Ws connect(String host, String pathAndQuery, int connectTimeoutMs,
                      int readTimeoutMs) throws IOException {
        SSLSocket s = (SSLSocket) SSLSocketFactory.getDefault().createSocket();
        s.connect(new java.net.InetSocketAddress(host, 443), connectTimeoutMs);
        s.setSoTimeout(readTimeoutMs);
        s.setTcpNoDelay(true);
        // SNI. SSLSocketFactory.createSocket() with no host does not set it,
        // and a CDN in front of the endpoint will hand back the wrong cert.
        try {
            javax.net.ssl.SSLParameters p = s.getSSLParameters();
            p.setServerNames(java.util.Collections.singletonList(
                (javax.net.ssl.SNIServerName) new javax.net.ssl.SNIHostName(host)));
            s.setSSLParameters(p);
        } catch (Throwable ignored) {}
        s.startHandshake();

        byte[] nonce = new byte[16];
        new SecureRandom().nextBytes(nonce);
        String key = android.util.Base64.encodeToString(nonce, android.util.Base64.NO_WRAP);
        String req = "GET " + pathAndQuery + " HTTP/1.1\r\n"
                   + "Host: " + host + "\r\n"
                   + "Upgrade: websocket\r\n"
                   + "Connection: Upgrade\r\n"
                   + "Sec-WebSocket-Key: " + key + "\r\n"
                   + "Sec-WebSocket-Version: 13\r\n"
                   + "User-Agent: phonebridge\r\n"
                   + "\r\n";
        OutputStream o = s.getOutputStream();
        o.write(req.getBytes(StandardCharsets.US_ASCII));
        o.flush();

        Ws ws = new Ws(s);
        String status = ws.readLine();
        if (status == null || !status.startsWith("HTTP/1.1 101")) {
            // Drain a little of the body so the error is readable, then bail.
            StringBuilder sb = new StringBuilder(status == null ? "" : status);
            String l;
            int n = 0;
            while ((l = ws.readLine()) != null && n++ < 30) sb.append('\n').append(l);
            ws.closeQuietly();
            throw new IOException("websocket upgrade refused: " + sb.toString().trim());
        }
        String accept = null;
        String l;
        while ((l = ws.readLine()) != null && !l.isEmpty()) {
            int c = l.indexOf(':');
            if (c > 0 && l.substring(0, c).trim().toLowerCase(Locale.US)
                          .equals("sec-websocket-accept")) {
                accept = l.substring(c + 1).trim();
            }
        }
        String want = expectedAccept(key);
        if (accept == null || !accept.equals(want)) {
            ws.closeQuietly();
            throw new IOException("websocket upgrade: bad Sec-WebSocket-Accept");
        }
        return ws;
    }

    private static String expectedAccept(String key) throws IOException {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            byte[] d = sha1.digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
                                   .getBytes(StandardCharsets.US_ASCII));
            return android.util.Base64.encodeToString(d, android.util.Base64.NO_WRAP);
        } catch (Exception e) {
            throw new IOException("no SHA-1", e);
        }
    }

    private String readLine() throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(128);
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') b.write(c);
        }
        if (c == -1 && b.size() == 0) return null;
        return new String(b.toByteArray(), StandardCharsets.US_ASCII);
    }

    // ---------------------------------------------------------------- send

    void sendText(String s) throws IOException {
        send(OP_TEXT, s.getBytes(StandardCharsets.UTF_8));
    }

    void sendBinary(byte[] b) throws IOException {
        send(OP_BINARY, b);
    }

    /** One masked, unfragmented frame. */
    void send(int opcode, byte[] payload) throws IOException {
        if (closed) throw new IOException("websocket closed");
        byte[] mask = new byte[4];
        rnd.nextBytes(mask);
        int len = payload.length;
        ByteArrayOutputStream f = new ByteArrayOutputStream(len + 14);
        f.write(0x80 | (opcode & 0x0F));           // FIN + opcode
        if (len < 126) {
            f.write(0x80 | len);                    // MASK bit + 7-bit length
        } else if (len < 65536) {
            f.write(0x80 | 126);
            f.write((len >> 8) & 0xFF);
            f.write(len & 0xFF);
        } else {
            f.write(0x80 | 127);
            for (int i = 7; i >= 0; i--) f.write((int) (((long) len >> (8 * i)) & 0xFF));
        }
        f.write(mask, 0, 4);
        byte[] masked = new byte[len];
        for (int i = 0; i < len; i++) masked[i] = (byte) (payload[i] ^ mask[i & 3]);
        f.write(masked, 0, len);
        synchronized (writeLock) {
            out.write(f.toByteArray());
            out.flush();
        }
    }

    // ---------------------------------------------------------------- recv

    /**
     * The next data frame. Pings are answered here and never surfaced; a
     * close frame is echoed and returned so the caller can stop. Returns
     * null on a clean end of stream.
     */
    Frame recv() throws IOException {
        while (true) {
            int b0 = in.read();
            if (b0 == -1) return null;
            int b1 = in.read();
            if (b1 == -1) return null;
            boolean fin = (b0 & 0x80) != 0;
            int opcode = b0 & 0x0F;
            boolean masked = (b1 & 0x80) != 0;
            long len = b1 & 0x7F;
            if (len == 126) {
                len = ((long) in.read() << 8) | in.read();
            } else if (len == 127) {
                len = 0;
                for (int i = 0; i < 8; i++) len = (len << 8) | in.read();
            }
            if (len < 0 || len > (16L << 20)) throw new IOException("frame too large: " + len);
            byte[] mask = null;
            if (masked) {                         // servers must not, but be tolerant
                mask = new byte[4];
                readFully(mask);
            }
            byte[] payload = new byte[(int) len];
            readFully(payload);
            if (mask != null) for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];

            switch (opcode) {
                case OP_PING:
                    try { send(OP_PONG, payload); } catch (IOException ignored) {}
                    continue;
                case OP_PONG:
                    continue;
                case OP_CLOSE:
                    closed = true;
                    try { send(OP_CLOSE, payload); } catch (IOException ignored) {}
                    return new Frame(OP_CLOSE, payload);
                case OP_CONT:
                    // Muse sends whole events. If a continuation ever arrives,
                    // say so loudly rather than silently splicing text.
                    throw new IOException("fragmented frame from server (unsupported)");
                default:
                    if (!fin) throw new IOException("fragmented frame from server (unsupported)");
                    return new Frame(opcode, payload);
            }
        }
    }

    private void readFully(byte[] b) throws IOException {
        int off = 0;
        while (off < b.length) {
            int n = in.read(b, off, b.length - off);
            if (n < 0) throw new IOException("stream ended mid-frame");
            off += n;
        }
    }

    // --------------------------------------------------------------- close

    void close() {
        if (!closed) {
            closed = true;
            try { send(OP_CLOSE, new byte[]{0x03, (byte) 0xE8}); } catch (Throwable ignored) {}
        }
        closeQuietly();
    }

    void closeQuietly() {
        closed = true;
        try { sock.close(); } catch (Throwable ignored) {}
    }

    boolean isClosed() {
        return closed;
    }
}
