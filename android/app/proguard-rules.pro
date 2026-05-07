# Keep nodejs-mobile JNI bindings.
-keep class com.janeasystems.rn_nodejs_mobile.** { *; }
-keep class org.nodejs.** { *; }

# Keep ML Kit / ZXing surfaces we reflect into.
-keep class com.google.mlkit.** { *; }
-keep class com.journeyapps.barcodescanner.** { *; }
