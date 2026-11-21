package io.github.ffdbackup;
import android.os.ParcelFileDescriptor;
interface IObbAccess {
    String[] listObbPaths(String packageName);
    ParcelFileDescriptor openObb(String packageName, String absolutePath);
    void destroy() = 16777114;
}
