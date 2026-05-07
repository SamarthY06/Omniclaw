package com.ben.history

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ben.R

class SessionDetailAdapter(private val items: List<DetailItem>) :
    RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    private val typeUser = 0
    private val typeAssistant = 1
    private val typeTool = 2

    override fun getItemViewType(position: Int) = when (items[position]) {
        is DetailItem.UserMessage -> typeUser
        is DetailItem.AssistantMessage -> typeAssistant
        is DetailItem.ToolMessage -> typeTool
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inf = LayoutInflater.from(parent.context)
        return when (viewType) {
            typeUser -> UserVH(inf.inflate(R.layout.item_message_user, parent, false))
            typeAssistant -> AssistantVH(inf.inflate(R.layout.item_message_assistant, parent, false))
            else -> ToolVH(inf.inflate(R.layout.item_message_tool, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val item = items[position]) {
            is DetailItem.UserMessage -> (holder as UserVH).text.text = item.text
            is DetailItem.AssistantMessage -> (holder as AssistantVH).text.text = item.text
            is DetailItem.ToolMessage -> {
                val h = holder as ToolVH
                h.summary.text = item.summary
                if (item.argsJson.isBlank()) {
                    h.args.visibility = View.GONE
                } else {
                    h.args.visibility = View.GONE // collapsed by default
                    h.summary.setOnClickListener {
                        h.args.visibility = if (h.args.visibility == View.VISIBLE) View.GONE else View.VISIBLE
                        h.args.text = item.argsJson
                    }
                }
            }
        }
    }

    override fun getItemCount(): Int = items.size

    class UserVH(v: View) : RecyclerView.ViewHolder(v) { val text: TextView = v.findViewById(R.id.msg_text) }
    class AssistantVH(v: View) : RecyclerView.ViewHolder(v) { val text: TextView = v.findViewById(R.id.msg_text) }
    class ToolVH(v: View) : RecyclerView.ViewHolder(v) {
        val summary: TextView = v.findViewById(R.id.tool_summary)
        val args: TextView = v.findViewById(R.id.tool_args)
    }
}
