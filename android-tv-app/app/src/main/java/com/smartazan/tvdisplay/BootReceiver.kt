package com.smartazan.tvdisplay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Auto-launches the display on boot, so a Fire TV with this set as its
 * screensaver/left running doesn't need a manual relaunch after a power
 * cycle or reboot. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val launch = Intent(context, MainActivity::class.java)
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(launch)
        }
    }
}
