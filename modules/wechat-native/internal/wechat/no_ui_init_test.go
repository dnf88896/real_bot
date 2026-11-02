package wechat
import("os";"path/filepath";"testing")
func TestDatabaseInitializationNeverSendsDesktopKeys(t *testing.T) {
 dir:=t.TempDir();marker:=filepath.Join(dir,"clicked")
 executable:="#!/bin/sh\necho invoked >> "+marker+"\n"
 if err:=os.WriteFile(filepath.Join(dir,"xdotool"),[]byte(executable),0755);err!=nil {t.Fatal(err)}
 t.Setenv("PATH",dir)
 if err:=dismissPostLoginOverlay();err!=nil {t.Fatal(err)}
 if _,err:=os.Stat(marker);!os.IsNotExist(err) {t.Fatal("initialization must never invoke xdotool")}
}
