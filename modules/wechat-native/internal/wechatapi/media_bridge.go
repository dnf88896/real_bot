package wechatapi

import (
 "crypto/sha256"
 "encoding/base64"
 "fmt"
 "net/http"
 "os"
 "path/filepath"
 "strings"
)

// Private authenticated byte bridge. The model never sees transport credentials
// and the bot service receives no access to the desktop's account databases.
func (server *Server) getMedia(response http.ResponseWriter, request *http.Request) {
 if !server.authenticate(response, request) { return }
 request.Body = http.MaxBytesReader(response, request.Body, 8192)
 var body struct { RoomID string `json:"roomid"`; Path string `json:"sdkfileid"` }
 if err := decodeJSON(request, &body); err != nil { writeError(response,400,"invalid media request"); return }
 if server.media == nil || !strings.HasPrefix(body.Path,"inbox/") { writeError(response,400,"invalid inbox reference"); return }
 path, _, err := server.media.ResolveImage(body.RoomID,body.Path)
 if err != nil { writeError(response,404,"image unavailable"); return }
 info, err := os.Stat(path)
 if err != nil || info.Size() <= 0 || info.Size() > 4*1024*1024 { writeError(response,413,"image size invalid"); return }
 data, err := os.ReadFile(path)
 if err != nil { writeError(response,404,"image unavailable"); return }
 writeJSON(response,200,map[string]any{"ret":0,"base64":base64.StdEncoding.EncodeToString(data)})
}

func (server *Server) putImage(response http.ResponseWriter, request *http.Request) {
 if !server.authenticate(response, request) { return }
 request.Body = http.MaxBytesReader(response,request.Body,6*1024*1024)
 var body struct { RoomID string `json:"roomid"`; Base64 string `json:"base64"` }
 if err := decodeJSON(request,&body); err != nil { writeError(response,400,"invalid image request"); return }
 if os.Getenv("WEBOX_PREVIEW_ONLY")=="1" && (os.Getenv("WEBOX_OWNER_ONLY")=="" || body.RoomID!=os.Getenv("WEBOX_OWNER_ONLY")) { writeError(response,403,"preview restricted to owner"); return }
 data, err := base64.StdEncoding.DecodeString(body.Base64)
 if err != nil || len(data)==0 || len(data)>4*1024*1024 { writeError(response,413,"image size invalid"); return }
 ext := map[string]string{"image/jpeg":".jpg","image/png":".png","image/gif":".gif","image/webp":".webp"}[http.DetectContentType(data)]
 if ext=="" || server.media==nil { writeError(response,400,"unsupported image"); return }
 room,err := server.media.RoomDirectory(body.RoomID)
 if err != nil { writeError(response,400,"invalid room"); return }
 ref := fmt.Sprintf("outbox/%x%s",sha256.Sum256(data),ext)
 path := filepath.Join(room,ref)
 // Never follow an existing symlink, even inside a private room directory.
 if info,e := os.Lstat(path); e==nil && !info.Mode().IsRegular() { writeError(response,400,"invalid media file"); return }
 tmp,err := os.CreateTemp(filepath.Join(room,"outbox"),".upload-*")
 if err != nil { writeError(response,500,"image store unavailable"); return }
 defer os.Remove(tmp.Name())
 _,err=tmp.Write(data); closeErr:=tmp.Close()
 if err!=nil || closeErr!=nil || os.Rename(tmp.Name(),path)!=nil { writeError(response,500,"image write failed"); return }
 writeJSON(response,200,map[string]any{"ret":0,"sdkfileid":ref})
}
