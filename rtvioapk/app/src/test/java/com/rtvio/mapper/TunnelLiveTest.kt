package com.rtvio.mapper

import com.rtvio.mapper.net.Protocol
import com.rtvio.mapper.net.Transport
import org.junit.Test
import java.io.DataInputStream

/**
 * Not a unit test: an on-demand check of the app's tunnel transport against a real
 * Studio. It does nothing unless RTVIO_TEST_STUDIO (e.g. http://127.0.0.1:8080) is set.
 */
class TunnelLiveTest {
    @Test
    fun tunnelSpeaksTheStreamProtocol() {
        val host = System.getenv("RTVIO_TEST_STUDIO") ?: return
        val pw = System.getenv("RTVIO_TEST_PW") ?: ""
        val sock = Transport.open(host, 5555, pw, 5000)
        val ack = Protocol.readHandshakeAck(DataInputStream(sock.getInputStream()))
        println("LIVE-TEST ack=$ack")
        Thread.sleep(500)
        sock.getOutputStream().write(Protocol.encodeStatus("""{"battery":50}"""))
        Thread.sleep(1500)
        sock.close()
    }
}
