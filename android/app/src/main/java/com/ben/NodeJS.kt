package com.ben

/**
 * Thin Kotlin wrapper around the JNI shim (`libbennode.so`) that hosts the
 * embedded `nodejs-mobile` runtime (`libnode.so`).
 *
 * Both shared libs are loaded eagerly at class init. The Node engine cannot
 * be restarted, so [startNode] must be called at most once per process; the
 * caller is expected to do that on a dedicated background thread.
 *
 * `arguments` follow the same convention as `node` CLI argv:
 *   ["node", "/data/.../index.js", "--maybe-flag"]
 *
 * Returns the exit code Node returned (0 on graceful shutdown).
 */
object NodeJS {
    init {
        // Order matters: bennode statically depends on the libnode symbols.
        System.loadLibrary("node")
        System.loadLibrary("bennode")
    }

    @JvmStatic
    external fun startNode(arguments: Array<String>): Int
}
