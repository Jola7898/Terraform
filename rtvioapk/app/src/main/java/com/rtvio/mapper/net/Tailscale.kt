package com.rtvio.mapper.net

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address
import java.net.NetworkInterface

/**
 * Is this phone on a tailnet?
 *
 * The Tailscale app does not expose which account it is signed in to, so the
 * app cannot compare accounts. What it can see is the two things that matter:
 * the phone has a tailnet address (Tailscale is installed, signed in and
 * switched on), and the Studio answers at the address in Settings (that PC is
 * on the same tailnet). Both together is "same account" for every practical
 * purpose - see [com.rtvio.mapper.ui.MainActivity]'s Studio mode.
 */
object Tailscale {

    const val PACKAGE = "com.tailscale.ipn"

    /** 100.64.0.0/10 - the carrier-grade-NAT range every tailnet address is drawn from. */
    fun isTailnetAddress(b: ByteArray): Boolean =
        b.size == 4 && (b[0].toInt() and 0xFF) == 100 && (b[1].toInt() and 0xC0) == 0x40

    /**
     * True for a tailnet IPv4 literal ("100.101.102.103") or a MagicDNS name
     * ("gpu-pc.tail1234.ts.net"), with or without a scheme, port or path.
     */
    fun isTailnetHost(raw: String): Boolean {
        val host = raw.trim().lowercase()
            .removePrefix("https://").removePrefix("http://")
            .substringBefore('/').substringBefore(':')
        if (host.isEmpty()) return false
        if (host.endsWith(".ts.net")) return true
        val parts = host.split('.')
        if (parts.size != 4) return false
        val bytes = parts.map { it.toIntOrNull() ?: return false }
        if (bytes.any { it !in 0..255 }) return false
        return isTailnetAddress(ByteArray(4) { bytes[it].toByte() })
    }

    /** Tailscale is up: some network (the VPN) or interface carries a tailnet address. */
    fun isUp(context: Context): Boolean {
        try {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            for (n in cm.allNetworks) {
                val caps = cm.getNetworkCapabilities(n) ?: continue
                if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
                val props = cm.getLinkProperties(n) ?: continue
                if (props.linkAddresses.any { (it.address as? Inet4Address)?.address?.let(::isTailnetAddress) == true }) return true
            }
        } catch (_: Exception) {
            // fall through to the interface scan
        }
        return try {
            NetworkInterface.getNetworkInterfaces().asSequence()
                .filter { it.isUp }
                .flatMap { it.inetAddresses.asSequence() }
                .any { it is Inet4Address && isTailnetAddress(it.address) }
        } catch (_: Exception) {
            false
        }
    }

    fun isInstalled(context: Context): Boolean = try {
        context.packageManager.getPackageInfo(PACKAGE, 0)
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    fun launchIntent(context: Context): Intent? = context.packageManager.getLaunchIntentForPackage(PACKAGE)
}
