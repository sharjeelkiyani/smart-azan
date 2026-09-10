package com.smartazan.tvdisplay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

/**
 * Starts the background azan watcher on boot, without forcing Smart Azan
 * itself onto the screen - the Fire TV boots to whatever it would normally
 * show (home screen, last app), and AzanOverlayService takes over the
 * display automatically only when there's actually an azan/dua to show.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            val serviceIntent = Intent(context, AzanOverlayService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent)
            } else {
                context.startService(serviceIntent)
            }
        }
    }
}
