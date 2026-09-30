package com.rtvio.mapper

import com.rtvio.mapper.net.Tailscale
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TailscaleTest {

    private fun ip(a: Int, b: Int, c: Int, d: Int) = byteArrayOf(a.toByte(), b.toByte(), c.toByte(), d.toByte())

    @Test
    fun tailnetRangeIs100_64_to_100_127() {
        assertTrue(Tailscale.isTailnetAddress(ip(100, 64, 0, 1)))
        assertTrue(Tailscale.isTailnetAddress(ip(100, 100, 127, 114)))
        assertTrue(Tailscale.isTailnetAddress(ip(100, 127, 255, 254)))
        assertFalse(Tailscale.isTailnetAddress(ip(100, 63, 255, 255)))
        assertFalse(Tailscale.isTailnetAddress(ip(100, 128, 0, 1)))
        assertFalse(Tailscale.isTailnetAddress(ip(10, 41, 19, 23)))
        assertFalse(Tailscale.isTailnetAddress(ip(192, 168, 137, 1)))
    }

    @Test
    fun hostsAreRecognisedWithOrWithoutSchemePortAndPath() {
        assertTrue(Tailscale.isTailnetHost("100.100.127.114"))
        assertTrue(Tailscale.isTailnetHost("http://100.100.127.114:8080/"))
        assertTrue(Tailscale.isTailnetHost("https://gpu-pc.tail1234.ts.net"))
        assertTrue(Tailscale.isTailnetHost("GPU-PC.TAIL1234.TS.NET:8080/x"))
        assertFalse(Tailscale.isTailnetHost("192.168.1.42"))
        assertFalse(Tailscale.isTailnetHost("https://studio.example.com"))
        assertFalse(Tailscale.isTailnetHost("100.100.1"))
        assertFalse(Tailscale.isTailnetHost("100.100.1.999"))
        assertFalse(Tailscale.isTailnetHost(""))
    }
}
