package com.rtvio.mapper.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * How the app reaches the desktop.
 *
 * - A bare host or IP ("192.168.1.5", "100.x.y.z") is a raw TCP connection to
 *   the receiver's port, exactly as before.
 * - An address starting with http:// or https:// ("https://gpu-pc.tailnet.ts.net")
 *   is the Studio's public URL, reached over a WebSocket at /ws/phone that
 *   carries the *same byte stream* (see [TunnelSocket]). That is what lets the
 *   phone stream through Tailscale Funnel or a Cloudflare tunnel, which pass
 *   HTTPS but not raw TCP - on any network, with nothing installed on the phone.
 *
 * Everything above this file (StreamClient, SessionTransferClient) only sees a
 * connected [Socket], so the wire protocol is untouched.
 */
object Transport {

    fun isTunnel(host: String): Boolean =
        host.startsWith("https://", true) || host.startsWith("http://", true)

    /** Returns a connected socket, raw TCP or tunnel; throws IOException if unreachable. */
    fun open(host: String, port: Int, password: String, timeoutMs: Int): Socket =
        if (isTunnel(host)) TunnelSocket(host, password).also { it.open(timeoutMs) }
        else Socket().apply {
            tcpNoDelay = true          // frames are latency-sensitive
            keepAlive = true
            sendBufferSize = 256 * 1024
            connect(InetSocketAddress(host, port), timeoutMs)
        }

    /** True if something answers at [host]. Tunnel: the Studio's open /api/health. */
    fun isReachable(host: String, port: Int, timeoutMs: Int): Boolean =
        if (isTunnel(host)) try {
            val http = OkHttpClient.Builder().callTimeout(timeoutMs.toLong(), TimeUnit.MILLISECONDS).build()
            http.newCall(Request.Builder().url(base(host) + "/api/health").build()).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            false
        }
        else try {
            Socket().use { it.connect(InetSocketAddress(host, port), timeoutMs) }
            true
        } catch (e: Exception) {
            false
        }

    internal fun base(host: String) = host.trim().trimEnd('/')
}

/**
 * A [Socket] whose bytes travel as WebSocket binary messages to the Studio,
 * which relays them to its phone TCP port. Only the parts the stream code uses
 * are implemented; message boundaries carry no meaning.
 */
class TunnelSocket(private val host: String, private val password: String) : Socket() {

    private companion object {
        val CLOSED = ByteArray(0)
        const val MAX_QUEUED_BYTES = 2L * 1024 * 1024
        const val SEND_SLICE = 64 * 1024
    }

    private val http = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)      // keeps idle links alive through proxies
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private val incoming = LinkedBlockingQueue<ByteArray>()
    private val closed = AtomicBoolean(false)
    @Volatile private var ws: WebSocket? = null
    @Volatile private var failure: String? = null
    @Volatile private var timeoutMs = 0
    private var pending: ByteArray? = null
    private var pendingPos = 0

    /** Signs in if needed, then opens the WebSocket; blocks until it is up. */
    fun open(connectTimeoutMs: Int) {
        val base = Transport.base(host)
        val token = login(base, connectTimeoutMs)
        val wsUrl = base.replaceFirst(Regex("^http", RegexOption.IGNORE_CASE), "ws") +
            "/ws/phone" + if (token.isNotEmpty()) "?token=$token" else ""
        val opened = CountDownLatch(1)
        ws = http.newWebSocket(Request.Builder().url(wsUrl).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = opened.countDown()
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                incoming.put(bytes.toByteArray())
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null); markClosed()
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = markClosed()
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                failure = when (response?.code) {
                    401 -> "wrong password"
                    503 -> "Studio is up but its phone receiver is not running"
                    null -> t.message ?: t.javaClass.simpleName
                    else -> "HTTP ${response.code}"
                }
                markClosed(); opened.countDown()
            }
        })
        if (!opened.await(connectTimeoutMs.toLong(), TimeUnit.MILLISECONDS) || failure != null) {
            val why = failure ?: "timed out"
            close()
            throw IOException("cannot reach $base: $why")
        }
    }

    private fun login(base: String, timeoutMs: Int): String {
        val client = http.newBuilder().callTimeout(timeoutMs.toLong(), TimeUnit.MILLISECONDS).build()
        val health = client.newCall(Request.Builder().url("$base/api/health").build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("$base answered HTTP ${r.code}")
            JSONObject(r.body?.string() ?: "{}")
        }
        if (!health.optBoolean("auth_required", true)) return ""
        if (password.isEmpty()) throw IOException("this Studio needs its password - set it in Settings")
        val body = JSONObject().put("password", password).toString().toRequestBody("application/json".toMediaType())
        client.newCall(Request.Builder().url("$base/api/login").post(body).build()).execute().use { r ->
            if (r.code == 401) throw IOException("wrong Studio password")
            if (!r.isSuccessful) throw IOException("sign-in failed: HTTP ${r.code}")
            return JSONObject(r.body?.string() ?: "{}").optString("token")
        }
    }

    private fun markClosed() {
        closed.set(true)
        incoming.offer(CLOSED)
    }

    // ---- the Socket surface the stream code touches -------------------------

    override fun setTcpNoDelay(on: Boolean) = Unit
    override fun setKeepAlive(on: Boolean) = Unit
    override fun setSendBufferSize(size: Int) = Unit
    override fun setSoTimeout(timeout: Int) { timeoutMs = timeout }
    override fun getSoTimeout(): Int = timeoutMs
    override fun isConnected(): Boolean = ws != null
    override fun isClosed(): Boolean = closed.get()

    override fun close() {
        if (closed.compareAndSet(false, true)) {
            incoming.offer(CLOSED)
            ws?.close(1000, null)
        }
        http.dispatcher.executorService.shutdown()
    }

    override fun getInputStream(): InputStream = object : InputStream() {
        override fun read(): Int {
            val one = ByteArray(1)
            return if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 0xFF
        }

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (len == 0) return 0
            var chunk = pending
            if (chunk == null) {
                chunk = if (timeoutMs > 0) incoming.poll(timeoutMs.toLong(), TimeUnit.MILLISECONDS)
                        else incoming.take()
                if (chunk == null) throw SocketTimeoutException("Read timed out")
                if (chunk === CLOSED) {
                    incoming.offer(CLOSED)      // stay at EOF for later reads
                    failure?.let { throw IOException(it) }
                    return -1
                }
                pending = chunk; pendingPos = 0
            }
            val n = minOf(len, chunk.size - pendingPos)
            System.arraycopy(chunk, pendingPos, b, off, n)
            pendingPos += n
            if (pendingPos >= chunk.size) pending = null
            return n
        }
    }

    override fun getOutputStream(): OutputStream = object : OutputStream() {
        override fun write(b: Int) = write(byteArrayOf(b.toByte()), 0, 1)

        override fun write(b: ByteArray, off: Int, len: Int) {
            var pos = off
            val end = off + len
            while (pos < end) {
                val n = minOf(SEND_SLICE, end - pos)
                // Blocking here is the backpressure: OkHttp buffers without
                // limit up to 16 MB and then kills the socket.
                while ((ws?.queueSize() ?: 0L) > MAX_QUEUED_BYTES) {
                    if (closed.get()) throw IOException(failure ?: "connection closed")
                    Thread.sleep(5)
                }
                if (closed.get() || ws?.send(b.toByteString(pos, n)) != true)
                    throw IOException(failure ?: "connection closed")
                pos += n
            }
        }

        override fun flush() = Unit
    }
}
