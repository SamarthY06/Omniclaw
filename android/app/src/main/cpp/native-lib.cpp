// JNI shim for nodejs-mobile (https://github.com/nodejs-mobile/nodejs-mobile).
//
// Exposes a single `Java_com_ben_NodeJS_startNode` symbol that mirrors the
// reference snippet from the official "Getting started" Android guide:
// https://nodejs-mobile.github.io/docs/guide/guide-android/getting-started/
//
// The Node engine cannot be restarted within a single process; the Kotlin side
// guarantees we only call this once.

#include <jni.h>
#include <string>
#include <cstring>
#include <cstdlib>
#include <android/log.h>
#include "node.h"

#define LOG_TAG "bennode"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

extern "C" JNIEXPORT jint JNICALL
Java_com_ben_NodeJS_startNode(
        JNIEnv *env,
        jobject /* this */,
        jobjectArray arguments) {
    const jsize argument_count = env->GetArrayLength(arguments);

    // Compute the byte size we need for argc-many NUL-terminated strings.
    int c_arguments_size = 0;
    for (int i = 0; i < argument_count; i++) {
        auto *jstr = (jstring) env->GetObjectArrayElement(arguments, i);
        const char *cstr = env->GetStringUTFChars(jstr, nullptr);
        c_arguments_size += strlen(cstr) + 1; // +1 for the trailing NUL
        env->ReleaseStringUTFChars(jstr, cstr);
        env->DeleteLocalRef(jstr);
    }

    // Allocate one contiguous buffer; libuv inside Node requires this.
    char *args_buffer = (char *) calloc(c_arguments_size, sizeof(char));
    if (args_buffer == nullptr) {
        LOGE("calloc(%d) failed for node argv buffer", c_arguments_size);
        return -1;
    }

    // argv pointers, one per arg.
    char **argv = (char **) calloc(argument_count, sizeof(char *));
    if (argv == nullptr) {
        free(args_buffer);
        LOGE("calloc(%d) failed for node argv pointer table", argument_count);
        return -1;
    }

    char *cursor = args_buffer;
    for (int i = 0; i < argument_count; i++) {
        auto *jstr = (jstring) env->GetObjectArrayElement(arguments, i);
        const char *cstr = env->GetStringUTFChars(jstr, nullptr);
        const size_t len = strlen(cstr);
        memcpy(cursor, cstr, len);
        cursor[len] = '\0';
        argv[i] = cursor;
        cursor += len + 1;
        env->ReleaseStringUTFChars(jstr, cstr);
        env->DeleteLocalRef(jstr);
    }

    LOGI("starting node engine with %d args (entry=%s)", argument_count,
         argument_count > 1 ? argv[1] : "<none>");
    const int rc = node::Start(argument_count, argv);
    LOGI("node engine exited with rc=%d", rc);

    free(argv);
    free(args_buffer);
    return (jint) rc;
}
