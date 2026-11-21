package io.github.ffdbackup;

import android.os.ParcelFileDescriptor;
import android.os.RemoteException;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Optional Shizuku UserService running with the permissions of the ADB shell user.
 *  Deliberately limits reads to .obb files under the known FFD package directory.
 */
public final class ObbUserService extends IObbAccess.Stub {
    private static final String TARGET_PACKAGE = "com.square_enix.android_googleplay.ffl_gp";
    private static final File OBB_ROOT = new File("/storage/emulated/0/Android/obb", TARGET_PACKAGE);

    public ObbUserService() { }

    private void assertPackage(String pkg) {
        if (!TARGET_PACKAGE.equals(pkg)) {
            throw new SecurityException("Unsupported application package");
        }
    }

    private File checkedFile(String pkg, String path) throws RemoteException {
        assertPackage(pkg);
        try {
            String root = OBB_ROOT.getCanonicalPath() + File.separator;
            File f = new File(path).getCanonicalFile();
            if (!f.getPath().startsWith(root) || !f.getName().toLowerCase(java.util.Locale.ROOT).endsWith(".obb")) {
                throw new SecurityException("Only FFD expansion files are permitted");
            }
            if (!f.isFile()) throw new RemoteException("OBB file is missing or inaccessible: " + f);
            return f;
        } catch (IOException e) {
            throw new RemoteException("Invalid OBB path: " + e);
        }
    }

    @Override public String[] listObbPaths(String packageName) throws RemoteException {
        assertPackage(packageName);
        if (!OBB_ROOT.exists()) return new String[0];
        File[] entries = OBB_ROOT.listFiles();
        if (entries == null) throw new RemoteException("Cannot list game OBB directory (shell access blocked)");
        List<String> paths = new ArrayList<>();
        for (File entry : entries) {
            if (entry.isFile() && entry.getName().toLowerCase(java.util.Locale.ROOT).endsWith(".obb")) {
                paths.add(entry.getAbsolutePath());
            }
        }
        String[] results = paths.toArray(new String[0]);
        Arrays.sort(results);
        return results;
    }

    @Override public ParcelFileDescriptor openObb(String packageName, String absolutePath) throws RemoteException {
        File f = checkedFile(packageName, absolutePath);
        try {
            return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY);
        } catch (IOException e) {
            throw new RemoteException("Could not read OBB file: " + e);
        }
    }

    @Override public void destroy() {
        System.exit(0);
    }
}
