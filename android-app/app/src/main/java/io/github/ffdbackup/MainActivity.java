package io.github.ffdbackup;

import android.app.Activity;
import android.content.ComponentName;
import android.content.ContentResolver;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.os.ParcelFileDescriptor;
import android.os.RemoteException;
import android.provider.DocumentsContract;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import rikka.shizuku.Shizuku;

/** Minimal read-only exporter. Does not modify or reinstall the target game. */
public final class MainActivity extends Activity {
    private static final String GAME = "com.square_enix.android_googleplay.ffl_gp";
    private static final int SELECT_DESTINATION = 100;
    private static final int SHIZUKU_PERMISSION = 101;

    private final ExecutorService background = Executors.newSingleThreadExecutor();
    private Uri destinationTree;
    private TextView logView;
    private TextView statusView;
    private Button exportButton;
    private volatile IObbAccess obbAccess;
    private boolean exporting = false;

    private final ServiceConnection shizukuConnection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder service) {
            obbAccess = IObbAccess.Stub.asInterface(service);
            postLog("Shizuku connected. Rootless OBB access enabled if your ROM allows it.");
        }
        @Override public void onServiceDisconnected(ComponentName name) {
            obbAccess = null;
            postLog("Shizuku disconnected; APK export remains available.");
        }
    };

    private final Shizuku.OnRequestPermissionResultListener permissionListener = (requestCode, grantResult) -> {
        if (requestCode != SHIZUKU_PERMISSION) return;
        if (grantResult == PackageManager.PERMISSION_GRANTED) bindShizuku();
        else postLog("Shizuku permission not granted; the APK-only export still works.");
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        int p = dp(18);
        body.setPadding(p, p, p, p);
        TextView header = new TextView(this);
        header.setText("FFD Backup · Rootless preservation");
        header.setTextSize(22);
        body.addView(header);
        TextView intro = new TextView(this);
        intro.setText("Copies original base/split APKs and, optionally, OBB expansion files. " +
                "Does not patch, uninstall, re-sign or alter Final Fantasy Dimensions.");
        intro.setPadding(0, dp(10), 0, dp(12));
        body.addView(intro);
        Button choose = new Button(this);
        choose.setText("1 · Choose backup destination");
        choose.setOnClickListener(v -> chooseDestination());
        body.addView(choose);
        Button connect = new Button(this);
        connect.setText("2 · Connect Shizuku (optional for OBB)");
        connect.setOnClickListener(v -> connectShizuku());
        body.addView(connect);
        exportButton = new Button(this);
        exportButton.setText("3 · Export original APKs + accessible OBBs");
        exportButton.setEnabled(false);
        exportButton.setOnClickListener(v -> beginExport());
        body.addView(exportButton);
        statusView = new TextView(this);
        statusView.setPadding(0, dp(8), 0, dp(8));
        statusView.setText("Select a destination folder to begin.");
        body.addView(statusView);
        ScrollView scroll = new ScrollView(this);
        logView = new TextView(this);
        logView.setTextIsSelectable(true);
        logView.setTextSize(13);
        logView.setPadding(dp(8), dp(8), dp(8), dp(8));
        scroll.addView(logView);
        body.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(body);
        try {
            Shizuku.addRequestPermissionResultListener(permissionListener);
        } catch (Throwable e) {
            appendLog("Shizuku library unavailable: " + e.getMessage());
        }
        describeGame();
    }

    @Override protected void onDestroy() {
        try { Shizuku.removeRequestPermissionResultListener(permissionListener); } catch (Throwable ignored) { }
        background.shutdown();
        super.onDestroy();
    }

    private int dp(int value) { return (int) (value * getResources().getDisplayMetrics().density + 0.5f); }

    private void appendLog(String message) { logView.append(message + "\n"); }
    private void postLog(String message) { runOnUiThread(() -> appendLog(message)); }

    private void describeGame() {
        try {
            PackageInfo pi = getPackageManager().getPackageInfo(GAME, 0);
            long code = Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode;
            appendLog("Installed game: " + GAME + " · version " + pi.versionName + " (code " + code + ")");
            ApplicationInfo ai = getPackageManager().getApplicationInfo(GAME, 0);
            appendLog("Base APK found: " + new File(ai.sourceDir).getName());
            appendLog("Split APK count: " + (ai.splitSourceDirs == null ? 0 : ai.splitSourceDirs.length));
        } catch (PackageManager.NameNotFoundException ex) {
            appendLog("Game not found under " + GAME + ". No files can be exported.");
        }
    }

    private void chooseDestination() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION |
                Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        startActivityForResult(intent, SELECT_DESTINATION);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != SELECT_DESTINATION || resultCode != RESULT_OK || data == null || data.getData() == null) return;
        destinationTree = data.getData();
        try {
            getContentResolver().takePersistableUriPermission(destinationTree,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        } catch (SecurityException ignored) { /* Current picker grant is still valid. */ }
        statusView.setText("Destination selected. You can export now.");
        exportButton.setEnabled(!exporting);
        appendLog("Backup directory selected (Storage Access Framework).");
    }

    private void connectShizuku() {
        try {
            if (!Shizuku.pingBinder()) {
                appendLog("Start the Shizuku app/service first (Wireless Debugging works without root).");
                return;
            }
            if (Shizuku.isPreV11()) {
                appendLog("Shizuku v11 or later is required.");
                return;
            }
            if (Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED) {
                bindShizuku();
            } else if (Shizuku.shouldShowRequestPermissionRationale()) {
                appendLog("Shizuku access was denied; grant access from Shizuku settings.");
            } else {
                Shizuku.requestPermission(SHIZUKU_PERMISSION);
            }
        } catch (Throwable e) {
            appendLog("Shizuku connection unavailable: " + e);
        }
    }

    private void bindShizuku() {
        try {
            Shizuku.UserServiceArgs args = new Shizuku.UserServiceArgs(
                    new ComponentName(getPackageName(), ObbUserService.class.getName()))
                    .daemon(false).processNameSuffix("ffd_obb").debuggable(false).version(1);
            Shizuku.bindUserService(args, shizukuConnection);
            appendLog("Connecting to Shizuku OBB reader…");
        } catch (Throwable e) {
            appendLog("Could not bind Shizuku service: " + e);
        }
    }

    private void beginExport() {
        if (exporting || destinationTree == null) return;
        exporting = true;
        exportButton.setEnabled(false);
        statusView.setText("Exporting… do not close the app during transfer.");
        final Uri rootTree = destinationTree;
        final IObbAccess access = obbAccess;
        background.submit(() -> {
            String outcome = "Export incomplete";
            try {
                outcome = exportInstalledFiles(rootTree, access);
            } catch (Exception ex) {
                postLog("Export error: " + ex.getClass().getSimpleName() + ": " + ex.getMessage());
            } finally {
                final String outcomeFinal = outcome;
                runOnUiThread(() -> {
                    exporting = false;
                    exportButton.setEnabled(destinationTree != null);
                    statusView.setText(outcomeFinal);
                });
            }
        });
    }

    private static String hex(byte[] input) {
        StringBuilder s = new StringBuilder(input.length * 2);
        for (byte b : input) s.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        return s.toString();
    }

    private Uri makeDir(Uri parent, String name) throws IOException {
        Uri result = DocumentsContract.createDocument(getContentResolver(), parent,
                DocumentsContract.Document.MIME_TYPE_DIR, name);
        if (result == null) throw new IOException("Provider could not create directory: " + name);
        return result;
    }

    private Uri makeFile(Uri parent, String name) throws IOException {
        Uri result = DocumentsContract.createDocument(getContentResolver(), parent,
                "application/octet-stream", name);
        if (result == null) throw new IOException("Provider could not create file: " + name);
        return result;
    }

    private Uri rootDocument(Uri tree) {
        return DocumentsContract.buildDocumentUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree));
    }

    private JSONObject copy(InputStream source, Uri folder, String name, String type) throws Exception {
        Uri target = makeFile(folder, name);
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        long count = 0;
        try (InputStream input = source;
             OutputStream output = getContentResolver().openOutputStream(target, "w")) {
            if (output == null) throw new IOException("Could not open destination: " + name);
            byte[] buffer = new byte[1 << 16];
            int size;
            while ((size = input.read(buffer)) != -1) {
                output.write(buffer, 0, size);
                md.update(buffer, 0, size);
                count += size;
            }
            output.flush();
        }
        JSONObject file = new JSONObject();
        file.put("path", ("apk".equals(type) ? "apks/" : "obb/") + name);
        file.put("type", type);
        file.put("bytes", count);
        file.put("sha256", hex(md.digest()));
        postLog("Copied " + name + " · " + count + " bytes · SHA-256 complete");
        return file;
    }

    private String exportInstalledFiles(Uri tree, IObbAccess access) throws Exception {
        PackageManager pm = getPackageManager();
        PackageInfo info = pm.getPackageInfo(GAME, 0);
        ApplicationInfo app = pm.getApplicationInfo(GAME, 0);
        List<File> apks = new ArrayList<>();
        apks.add(new File(app.sourceDir));
        if (app.splitSourceDirs != null) {
            for (String path : app.splitSourceDirs) apks.add(new File(path));
        }
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date());
        Uri backup = makeDir(rootDocument(tree), "FFD_Backup_" + stamp);
        Uri apkFolder = makeDir(backup, "apks");
        Uri obbFolder = makeDir(backup, "obb");
        JSONArray files = new JSONArray();
        JSONArray errors = new JSONArray();
        JSONObject manifest = new JSONObject();
        manifest.put("tool", "FFD Backup Toolkit for Android");
        manifest.put("schema", 1);
        manifest.put("package", GAME);
        manifest.put("version_name", info.versionName);
        manifest.put("version_code", Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode);
        manifest.put("files", files);
        manifest.put("errors", errors);
        manifest.put("saves_included", false);
        manifest.put("original_files_untouched", true);
        postLog("Backing up " + apks.size() + " installed APK file(s)…");
        for (File apk : apks) {
            try {
                files.put(copy(new FileInputStream(apk), apkFolder, apk.getName(), "apk"));
            } catch (Exception ex) {
                errors.put("APK " + apk.getName() + ": " + ex);
                postLog("ERROR copying " + apk.getName() + ": " + ex.getMessage());
            }
        }
        String obbStatus;
        if (access == null) {
            obbStatus = "not_attempted_shizuku_not_connected";
            postLog("OBBs not attempted: connect Shizuku before exporting to include them.");
        } else {
            try {
                String[] paths = access.listObbPaths(GAME);
                obbStatus = paths == null || paths.length == 0 ? "none_found" : "found";
                if (paths != null) for (String path : paths) {
                    String name = new File(path).getName();
                    try {
                        ParcelFileDescriptor fd = access.openObb(GAME, path);
                        if (fd == null) throw new IOException("Shizuku did not return a file descriptor");
                        files.put(copy(new ParcelFileDescriptor.AutoCloseInputStream(fd),
                                obbFolder, name, "obb"));
                    } catch (Exception ex) {
                        errors.put("OBB " + name + ": " + ex);
                        postLog("ERROR copying OBB " + name + ": " + ex.getMessage());
                    }
                }
                postLog("OBB status: " + obbStatus);
            } catch (RemoteException | SecurityException ex) {
                obbStatus = "inaccessible_by_shizuku_shell";
                errors.put("OBB access: " + ex);
                postLog("OBB access unavailable on this ROM: " + ex.getMessage());
            }
        }
        manifest.put("obb_status", obbStatus);
        String status = errors.length() > 0 ? "partial_errors" :
                "not_attempted_shizuku_not_connected".equals(obbStatus) ? "apks_only" : "completed";
        manifest.put("status", status);
        Uri meta = makeFile(backup, "manifest.json");
        try (OutputStream output = getContentResolver().openOutputStream(meta, "w")) {
            if (output == null) throw new IOException("Cannot open manifest output");
            output.write((manifest.toString(2) + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
        String outcome = "Export " + status + ": " + files.length() + " files. Browse the chosen folder for FFD_Backup_" + stamp;
        postLog(outcome);
        return outcome;
    }
}
