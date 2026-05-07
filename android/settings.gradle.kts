pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // android-vad (used by SessionTimer for VAD frames) is hosted on JitPack.
        maven { url = uri("https://jitpack.io") }
    }
}

rootProject.name = "Ben"
include(":app")
