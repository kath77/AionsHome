package com.aion.chat;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;

/**
 * 启动页 — 选择连接地址（家庭WiFi / 户外Tailscale）
 */
public class LauncherActivity extends AppCompatActivity {

    private static final String PREFS       = "aion_prefs";
    private static final String KEY_URL     = "saved_url";
    private static final String KEY_AUTO    = "auto_connect";
    private static final String KEY_OUTDOOR = "outdoor_url";

    // ★ 在这里修改你的两个地址
    private static final String URL_HOME    = "http://192.168.0.101:8000/chat";
    private static final String URL_OUTDOOR = "http://100.65.110.18:8000/chat";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        // 如果上次勾选了"记住选择"，直接跳转
        if (prefs.getBoolean(KEY_AUTO, false)) {
            String savedUrl = normalizeUrl(prefs.getString(KEY_URL, URL_HOME));
            prefs.edit().putString(KEY_URL, savedUrl).apply();
            launchWebView(savedUrl);
            return;
        }

        setContentView(R.layout.activity_launcher);

        TextView tvHome    = findViewById(R.id.tvHomeUrl);
        TextView tvOutdoor = findViewById(R.id.tvOutdoorUrl);
        Button   btnHome   = findViewById(R.id.btnHome);
        Button   btnOutdoor= findViewById(R.id.btnOutdoor);
        CheckBox cbRemember= findViewById(R.id.cbRemember);
        String outdoorUrl = normalizeUrl(prefs.getString(KEY_OUTDOOR, URL_OUTDOOR));
        prefs.edit().putString(KEY_OUTDOOR, outdoorUrl).apply();

        tvHome.setText(URL_HOME);
        tvOutdoor.setText(outdoorUrl);

        btnHome.setOnClickListener(v -> {
            saveIfNeeded(prefs, cbRemember.isChecked(), URL_HOME);
            launchWebView(URL_HOME);
        });

        btnOutdoor.setOnClickListener(v -> {
            String currentOutdoor = normalizeUrl(prefs.getString(KEY_OUTDOOR, URL_OUTDOOR));
            saveIfNeeded(prefs, cbRemember.isChecked(), currentOutdoor);
            launchWebView(currentOutdoor);
        });

        btnOutdoor.setOnLongClickListener(v -> {
            showOutdoorEditDialog(prefs, tvOutdoor);
            return true;
        });
    }

    private void saveIfNeeded(SharedPreferences prefs, boolean remember, String url) {
        SharedPreferences.Editor editor = prefs.edit();
        editor.putString(KEY_URL, url);
        editor.putBoolean(KEY_AUTO, remember);
        editor.apply();
    }

    private void launchWebView(String url) {
        String normalizedUrl = normalizeUrl(url);

        // 启动前台推送服务
        startPushService(normalizedUrl);

        Intent intent = new Intent(this, WebViewActivity.class);
        intent.putExtra("url", normalizedUrl);
        startActivity(intent);
        finish();
    }

    private void startPushService(String url) {
        // 启动前台服务（权限请求移到 WebViewActivity，因为本 Activity 会立即 finish）
        Intent serviceIntent = new Intent(this, AionPushService.class);
        serviceIntent.putExtra("url", url);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent);
        } else {
            startService(serviceIntent);
        }
    }

    private String normalizeUrl(String url) {
        if (url == null || url.isEmpty()) return URL_HOME;
        if (url.contains("127.0.0.1") || url.contains("localhost")) return URL_HOME;
        return url;
    }

    private void showOutdoorEditDialog(SharedPreferences prefs, TextView tvOutdoor) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setText(prefs.getString(KEY_OUTDOOR, URL_OUTDOOR));
        input.setSelection(input.getText().length());

        new AlertDialog.Builder(this)
                .setTitle("设置户外地址")
                .setMessage("请输入 Tailscale 地址，例如 http://100.x.x.x:8000/chat")
                .setView(input)
                .setNegativeButton("取消", null)
                .setPositiveButton("保存", (dialog, which) -> {
                    String raw = input.getText().toString().trim();
                    if (raw.isEmpty()) raw = URL_OUTDOOR;
                    String normalized = normalizeUrl(raw);
                    prefs.edit().putString(KEY_OUTDOOR, normalized).apply();
                    tvOutdoor.setText(normalized);
                    Toast.makeText(this, "户外地址已保存", Toast.LENGTH_SHORT).show();
                })
                .show();
    }
}
