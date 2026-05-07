# CMake glue for the embedded Node.js runtime.
#
# This builds a tiny `libbennode.so` JNI bridge that:
#   1. Imports the prebuilt `libnode.so` (one per ABI) from app/libnode/bin/.
#   2. Exposes `Java_com_ben_NodeJS_startNode(...)` to Kotlin so we can call
#      node::Start(argc, argv) from a background thread.
#
# Layout (set up by android/scripts/fetch-nodejs-mobile.sh):
#
#   app/libnode/include/node/   <- node.h, ...
#   app/libnode/bin/<abi>/libnode.so

cmake_minimum_required(VERSION 3.18.1)
project(bennode)

# Headers
include_directories(${CMAKE_SOURCE_DIR}/../../../libnode/include/node/)

# Imported libnode.so per ABI (CMake picks the right one via ANDROID_ABI).
add_library(libnode SHARED IMPORTED)
set_target_properties(libnode PROPERTIES IMPORTED_LOCATION
    ${CMAKE_SOURCE_DIR}/../../../libnode/bin/${ANDROID_ABI}/libnode.so)

# Our JNI shim
add_library(bennode SHARED native-lib.cpp)

find_library(log-lib log)

target_link_libraries(bennode libnode ${log-lib})
