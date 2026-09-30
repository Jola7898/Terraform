package com.rtvio.mapper.net

import com.rtvio.mapper.data.SettingsManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * The slice of RTVIO Studio's HTTP API the app uses directly: is it up, start
 * and stop a take (with or without live reconstruction), switch image
 * enhancement, and read job progress. Everything else - the sessions list, the
 * 3D viewer, uploads - is the Studio website itself, shown in the app's
 * WebView.
 *
 * The Studio lives at http://<Server IP>:<Studio port> (a tailnet address in
 * Studio mode), or at the https:// address when Server IP is one.
 */
class StudioApi(private val settings: SettingsManager) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()
    private val json = "application/json".toMediaType()

    @Volatile private var token: String? = null
    @Volatile private var tokenFor: String = ""

    val baseUrl: String
        get() {
            val h = settings.serverIp
            return if (Transport.isTunnel(h)) Transport.base(h) else "http://$h:${settings.studioPort}"
        }

    /** True if the Studio answers its open /api/health within [timeoutMs]. */
    suspend fun isUp(timeoutMs: Long = 1500): Boolean = withContext(Dispatchers.IO) {
        if (settings.serverIp.isEmpty()) return@withContext false
        try {
            val c = http.newBuilder().callTimeout(timeoutMs, TimeUnit.MILLISECONDS).build()
            c.newCall(Request.Builder().url("$baseUrl/api/health").build()).execute().use { r ->
                r.isSuccessful && (r.body?.string()?.contains("rtvio-studio") == true)
            }
        } catch (_: Exception) {
            false
        }
    }

    /**
     * The session token ("" when the Studio has no password). Cached until the
     * address or password changes. Throws IOException with a message fit to show.
     */
    suspend fun authToken(): String = withContext(Dispatchers.IO) {
        val key = baseUrl + "|" + settings.serverPassword
        token?.let { if (tokenFor == key) return@withContext it }
        val health = http.newCall(Request.Builder().url("$baseUrl/api/health").build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("Studio answered HTTP ${r.code}")
            JSONObject(r.body?.string() ?: "{}")
        }
        val t = if (!health.optBoolean("auth_required", true)) "" else {
            val pw = settings.serverPassword
            if (pw.isEmpty()) throw IOException("This Studio needs its password - set it in Settings")
            val body = JSONObject().put("password", pw).toString().toRequestBody(json)
            http.newCall(Request.Builder().url("$baseUrl/api/login").post(body).build()).execute().use { r ->
                if (r.code == 401) throw IOException("Wrong Studio password")
                if (!r.isSuccessful) throw IOException("Sign-in failed: HTTP ${r.code}")
                JSONObject(r.body?.string() ?: "{}").optString("token")
            }
        }
        token = t
        tokenFor = key
        t
    }

    suspend fun getJson(path: String): JSONObject = call("GET", path, null)

    suspend fun postJson(path: String, body: JSONObject): JSONObject = call("POST", path, body)

    private suspend fun call(method: String, path: String, body: JSONObject?): JSONObject {
        val t = authToken()
        return withContext(Dispatchers.IO) {
            val req = Request.Builder().url(baseUrl + path)
                .apply { if (t.isNotEmpty()) header("Authorization", "Bearer $t") }
                .method(method, body?.toString()?.toRequestBody(json) ?: if (method == "POST") "{}".toRequestBody(json) else null)
                .build()
            http.newCall(req).execute().use { r ->
                val text = r.body?.string().orEmpty()
                val obj = try { JSONObject(text) } catch (_: Exception) { JSONObject() }
                if (r.code == 401) { token = null; throw IOException("Signed out - check the Studio password") }
                if (!r.isSuccessful) throw IOException(obj.optString("error").ifEmpty { "HTTP ${r.code}" })
                obj
            }
        }
    }

    /** Same switch as the website's "Enhance image quality" checkbox for phone takes. */
    suspend fun setEnhance(on: Boolean) {
        postJson("/api/settings", JSONObject().put("recon", JSONObject().put("enhance", on)))
    }

    /** Starts a take on the connected phone; returns its session id. */
    suspend fun startRecording(live: Boolean): String =
        postJson("/api/record/start", JSONObject().put("live", live)).optString("session")

    suspend fun stopRecording() {
        postJson("/api/record/stop", JSONObject())
    }

    /** What the Studio is doing with [sessionId]'s reconstruction. */
    data class Progress(val state: String, val detail: String, val fraction: Double, val live: Boolean, val error: String)

    /** Reads /api/state: the newest job for [sessionId] (null if none) and the id of a take in progress. */
    suspend fun poll(sessionId: String?): Pair<Progress?, String?> {
        val st = getJson("/api/state")
        val recId = st.optJSONObject("phone")?.optJSONObject("recording")?.optString("id")?.ifEmpty { null }
        val target = sessionId ?: recId
        val jobs: JSONArray = st.optJSONArray("jobs") ?: JSONArray()
        var found: Progress? = null
        for (i in 0 until jobs.length()) {
            val j = jobs.optJSONObject(i) ?: continue
            if (target == null || j.optString("session") != target) continue
            val pr = j.optJSONObject("progress")
            found = Progress(
                state = j.optString("state"),
                detail = pr?.optString("detail").orEmpty(),
                fraction = pr?.optDouble("fraction", 0.0) ?: 0.0,
                live = j.optString("kind") == "live",
                error = j.optString("error").let { if (it == "null") "" else it },
            )
        }
        return found to recId
    }
}
