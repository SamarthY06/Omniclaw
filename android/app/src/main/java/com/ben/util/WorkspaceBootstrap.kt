package com.ben.util

import android.content.Context
import android.content.res.AssetManager
import android.util.Log
import java.io.File
import java.io.FileOutputStream

// On first app launch, copy our embedded Node payload (assets/node/) into
// filesDir/node/ so it can be require()-ed at runtime, and seed the workspace
// (filesDir/openclaw/workspace) from assets/node/workspace_bootstrap/.
//
// Subsequent launches re-validate that the bootstrap layout is intact and copy
// anything that's missing so a partially-broken state self-heals.
object WorkspaceBootstrap {
    private const val TAG = "WorkspaceBootstrap"
    private const val NODE_DIR_NAME = "node"
    private const val WORKSPACE_DIR_NAME = "openclaw/workspace"
    private const val ASSET_NODE_ROOT = "node"
    private const val ASSET_WORKSPACE_BOOTSTRAP = "node/workspace_bootstrap"

    fun ensureNodeRoot(ctx: Context): File {
        val dest = File(ctx.filesDir, NODE_DIR_NAME)
        if (!dest.exists() || !File(dest, "index.js").exists()) {
            dest.mkdirs()
            copyAssetTree(ctx.assets, ASSET_NODE_ROOT, dest)
        }
        return dest
    }

    fun ensureWorkspace(ctx: Context): File {
        val dest = File(ctx.filesDir, WORKSPACE_DIR_NAME)
        if (!dest.exists() || !File(dest, "AGENTS.md").exists()) {
            dest.mkdirs()
            copyAssetTree(ctx.assets, ASSET_WORKSPACE_BOOTSTRAP, dest)
        }
        return dest
    }

    private fun copyAssetTree(assets: AssetManager, srcPath: String, dst: File) {
        val children = try {
            assets.list(srcPath) ?: emptyArray()
        } catch (e: Exception) {
            Log.w(TAG, "list($srcPath) failed: ${e.message}")
            return
        }
        if (children.isEmpty()) {
            try {
                assets.open(srcPath).use { input ->
                    FileOutputStream(dst).use { output ->
                        input.copyTo(output)
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "copy($srcPath -> $dst) failed: ${e.message}")
            }
            return
        }
        if (!dst.exists()) dst.mkdirs()
        for (child in children) {
            val childSrc = if (srcPath.isEmpty()) child else "$srcPath/$child"
            val childDst = File(dst, child)
            copyAssetTree(assets, childSrc, childDst)
        }
    }
}
