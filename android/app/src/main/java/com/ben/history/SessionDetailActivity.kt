package com.ben.history

import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.ben.R
import com.ben.util.WorkspaceBootstrap
import com.google.android.material.textfield.TextInputEditText
import org.json.JSONObject
import java.io.File

class SessionDetailActivity : AppCompatActivity() {
    private val all = ArrayList<DetailItem>()
    private val filtered = ArrayList<DetailItem>()
    private val adapter = SessionDetailAdapter(filtered)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_session_detail)

        val list = findViewById<RecyclerView>(R.id.detail_list)
        list.layoutManager = LinearLayoutManager(this).apply { stackFromEnd = false }
        list.adapter = adapter

        val search = findViewById<TextInputEditText>(R.id.detail_search)
        search.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) { applyFilter(s?.toString().orEmpty()) }
        })

        val rel = intent.getStringExtra(EXTRA_PATH).orEmpty()
        val ws = WorkspaceBootstrap.ensureWorkspace(this)
        val file = File(ws, rel)
        if (file.exists()) {
            file.useLines { lines ->
                lines.forEach { line ->
                    val parsed = runCatching { JSONObject(line) }.getOrNull() ?: return@forEach
                    DetailItem.parse(parsed)?.let { all.add(it) }
                }
            }
        }
        applyFilter("")
    }

    private fun applyFilter(query: String) {
        filtered.clear()
        if (query.isBlank()) {
            filtered.addAll(all)
        } else {
            val q = query.lowercase()
            filtered.addAll(all.filter { it.searchKey.contains(q) })
        }
        adapter.notifyDataSetChanged()
    }

    companion object {
        const val EXTRA_PATH = "path"
    }
}

sealed class DetailItem {
    abstract val searchKey: String

    data class UserMessage(val text: String) : DetailItem() {
        override val searchKey = text.lowercase()
    }
    data class AssistantMessage(val text: String) : DetailItem() {
        override val searchKey = text.lowercase()
    }
    data class ToolMessage(val summary: String, val argsJson: String) : DetailItem() {
        override val searchKey = (summary + " " + argsJson).lowercase()
    }

    companion object {
        fun parse(o: JSONObject): DetailItem? {
            return when (o.optString("type")) {
                "user.text" -> UserMessage(o.optString("text"))
                "assistant.text" -> AssistantMessage(o.optString("text"))
                "tool.call" -> ToolMessage(
                    summary = "called ${o.optString("name")}.${o.optString("subcommand")}",
                    argsJson = o.optJSONObject("args")?.toString(2) ?: "",
                )
                "tool.result" -> ToolMessage(
                    summary = (if (o.optBoolean("ok", false)) "ok" else "fail") +
                        " ${o.optString("name")}: ${o.optString("summary")}",
                    argsJson = "",
                )
                else -> null
            }
        }
    }
}
