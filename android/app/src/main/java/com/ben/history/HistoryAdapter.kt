package com.ben.history

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ben.R

class HistoryAdapter(
    private val items: List<HistoryEntry>,
    private val onClick: (HistoryEntry) -> Unit,
) : RecyclerView.Adapter<HistoryAdapter.VH>() {

    class VH(v: View) : RecyclerView.ViewHolder(v) {
        val startedAt: TextView = v.findViewById(R.id.history_started_at)
        val device: TextView = v.findViewById(R.id.history_device)
        val firstLine: TextView = v.findViewById(R.id.history_first_user_line)
        val meta: TextView = v.findViewById(R.id.history_meta)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val v = LayoutInflater.from(parent.context).inflate(R.layout.item_history_card, parent, false)
        return VH(v)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val e = items[position]
        holder.startedAt.text = e.startedAtRelative()
        holder.device.text = "• ${e.device}"
        holder.firstLine.text = e.firstUserLine.ifBlank { "(no transcription)" }
        val tools = if (e.toolsUsed.isEmpty()) "" else " • ${e.toolsUsed.joinToString(", ")}"
        holder.meta.text = e.durationLabel() + tools
        holder.itemView.setOnClickListener { onClick(e) }
    }

    override fun getItemCount(): Int = items.size
}
