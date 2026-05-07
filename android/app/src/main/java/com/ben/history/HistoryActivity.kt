package com.ben.history

import android.content.Intent
import android.os.Bundle
import android.os.FileObserver
import android.view.View
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import com.ben.R
import com.ben.util.WorkspaceBootstrap
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class HistoryActivity : AppCompatActivity() {
    private lateinit var list: RecyclerView
    private lateinit var swipe: SwipeRefreshLayout
    private lateinit var empty: TextView
    private val items = ArrayList<HistoryEntry>()
    private val adapter = HistoryAdapter(items) { entry ->
        startActivity(
            Intent(this, SessionDetailActivity::class.java)
                .putExtra(SessionDetailActivity.EXTRA_PATH, entry.path),
        )
    }
    private var observer: FileObserver? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_history)
        list = findViewById(R.id.history_list)
        swipe = findViewById(R.id.history_swipe)
        empty = findViewById(R.id.history_empty)
        list.layoutManager = LinearLayoutManager(this)
        list.adapter = adapter
        swipe.setOnRefreshListener { reload() }
        reload()
    }

    override fun onResume() {
        super.onResume()
        val workspace = WorkspaceBootstrap.ensureWorkspace(this)
        val sessionsDir = File(workspace, "sessions").apply { mkdirs() }
        observer = object : FileObserver(sessionsDir, MODIFY or CREATE or CLOSE_WRITE) {
            override fun onEvent(event: Int, path: String?) {
                runOnUiThread { reload() }
            }
        }.also { it.startWatching() }
        reload()
    }

    override fun onPause() {
        super.onPause()
        observer?.stopWatching()
        observer = null
    }

    private fun reload() {
        val workspace = WorkspaceBootstrap.ensureWorkspace(this)
        val indexFile = File(workspace, "sessions/index.jsonl")
        items.clear()
        if (indexFile.exists()) {
            indexFile.useLines { lines ->
                lines.forEach { line ->
                    val parsed = runCatching { JSONObject(line) }.getOrNull() ?: return@forEach
                    items.add(HistoryEntry.fromIndexJson(parsed))
                }
            }
        }
        items.sortByDescending { it.startedAtMs }
        adapter.notifyDataSetChanged()
        empty.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
        swipe.isRefreshing = false
    }
}

data class HistoryEntry(
    val id: String,
    val startedAtMs: Long,
    val durationMs: Long,
    val firstUserLine: String,
    val device: String,
    val toolsUsed: List<String>,
    val path: String,
) {
    fun startedAtRelative(): String {
        val now = System.currentTimeMillis()
        val delta = now - startedAtMs
        if (delta < 60_000) return "just now"
        if (delta < 3600_000) return "${delta / 60_000} min ago"
        if (delta < 86_400_000) return "${delta / 3_600_000} h ago"
        val sdf = SimpleDateFormat("MMM d, HH:mm", Locale.getDefault())
        return sdf.format(Date(startedAtMs))
    }

    fun durationLabel(): String {
        if (durationMs < 60_000) return "${durationMs / 1000}s"
        return "${durationMs / 60_000}m ${(durationMs / 1000) % 60}s"
    }

    companion object {
        fun fromIndexJson(o: JSONObject): HistoryEntry {
            val started = parseTs(o.optString("started_at", "")) ?: 0L
            return HistoryEntry(
                id = o.optString("id"),
                startedAtMs = started,
                durationMs = o.optLong("duration_ms", 0L),
                firstUserLine = o.optString("first_user_line", ""),
                device = o.optString("device", "phone"),
                toolsUsed = run {
                    val ja = o.optJSONArray("tools_used")
                    val out = ArrayList<String>()
                    if (ja != null) for (i in 0 until ja.length()) out.add(ja.optString(i))
                    out
                },
                path = o.optString("path", ""),
            )
        }

        private fun parseTs(s: String): Long? {
            return try {
                java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
            } catch (_: Exception) {
                null
            }
        }
    }
}
