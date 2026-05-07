package com.ben.ui

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.ben.R
import com.ben.bridge.NodeRpcProbe
import com.ben.util.BenSecrets

/**
 * Home tab. Shows current session status, embedded-node health, peer status,
 * and a "Start now" button (synthesizes a wake-word fire so the user can test
 * Realtime without saying "Ben").
 *
 * The idle status string is formatted with the user's configured wake phrase
 * (BenSecrets.wakePhrase). When SettingsActivity changes the phrase it
 * broadcasts ACTION_WAKE_PHRASE_CHANGED via LocalBroadcastManager and the
 * receiver below re-renders without requiring a fragment restart.
 */
class HomeFragment : Fragment() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var statusView: TextView
    private lateinit var nodeView: TextView
    private lateinit var peerView: TextView

    private val phraseChangedReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            renderStatusIdle()
        }
    }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, b: Bundle?): View {
        return inflater.inflate(R.layout.fragment_home, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        statusView = view.findViewById(R.id.home_status)
        nodeView = view.findViewById(R.id.home_node_status)
        peerView = view.findViewById(R.id.home_peer_status)
        val startBtn = view.findViewById<Button>(R.id.home_start_now)
        startBtn.setOnClickListener {
            val ctx = requireContext()
            val intent = Intent(ctx, com.ben.service.BenVoiceService::class.java)
                .setAction(com.ben.service.BenVoiceService.ACTION_START_FROM_USER)
            // BenVoiceService is intentionally NOT a foreground service in
            // 0.1.2 (mic FGS is anchored by BenForegroundService). Calling
            // startForegroundService here would raise RemoteServiceException
            // because BenVoiceService.onCreate doesn't call startForeground().
            ctx.startService(intent)
        }
        view.findViewById<Button>(R.id.home_mic_test)?.setOnClickListener {
            startActivity(Intent(requireContext(), com.ben.diag.MicTestActivity::class.java))
        }
        renderStatusIdle()
        pollStatus()
    }

    override fun onResume() {
        super.onResume()
        // Picks up wake-phrase edits made in SettingsActivity even if the
        // broadcast was missed (e.g. fragment was paused).
        renderStatusIdle()
        LocalBroadcastManager.getInstance(requireContext())
            .registerReceiver(phraseChangedReceiver, IntentFilter(ACTION_WAKE_PHRASE_CHANGED))
    }

    override fun onPause() {
        super.onPause()
        try {
            LocalBroadcastManager.getInstance(requireContext()).unregisterReceiver(phraseChangedReceiver)
        } catch (_: IllegalArgumentException) { /* not registered */ }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        handler.removeCallbacksAndMessages(null)
    }

    private fun renderStatusIdle() {
        if (!isAdded) return
        val phrase = BenSecrets.wakePhrase(requireContext())
        statusView.text = getString(R.string.home_status_idle, phrase)
    }

    private fun pollStatus() {
        val ctx = requireContext()
        Thread {
            val probe = NodeRpcProbe.ping(ctx)
            handler.post {
                if (!isAdded) return@post
                nodeView.text = getString(R.string.home_node_status, if (probe) "ok" else "starting…")
                val peerHost = BenSecrets.peerHost(ctx)
                peerView.text = getString(
                    R.string.home_peer_status,
                    if (peerHost == null) "not paired" else "paired with $peerHost",
                )
            }
        }.start()
        handler.postDelayed({ pollStatus() }, 3_000)
    }

    companion object {
        const val ACTION_WAKE_PHRASE_CHANGED = "com.ben.action.WAKE_PHRASE_CHANGED"
    }
}
