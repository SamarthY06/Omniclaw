package com.ben

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.ben.history.HistoryActivity
import com.ben.onboarding.OnboardingActivity
import com.ben.settings.SettingsActivity
import com.ben.ui.HomeFragment
import com.ben.util.BenSecrets
import com.google.android.material.bottomnavigation.BottomNavigationView

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // First-launch -> push the user through onboarding before they see Home.
        if (!BenSecrets.isOnboardingComplete(this)) {
            startActivity(Intent(this, OnboardingActivity::class.java))
            finish()
            return
        }

        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.main_fragment_container, HomeFragment())
                .commit()
        }

        val nav = findViewById<BottomNavigationView>(R.id.bottom_nav)
        nav.selectedItemId = R.id.nav_home
        nav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_home -> {
                    supportFragmentManager.beginTransaction()
                        .replace(R.id.main_fragment_container, HomeFragment())
                        .commit()
                    true
                }
                R.id.nav_history -> {
                    startActivity(Intent(this, HistoryActivity::class.java))
                    false
                }
                R.id.nav_settings -> {
                    startActivity(Intent(this, SettingsActivity::class.java))
                    false
                }
                else -> false
            }
        }
    }
}
