package wechatapi

import (
 "bytes"
 "context"
 "encoding/json"
 "errors"
 "io"
 "net/http"
 "os"
 "strings"
 "time"
)

type mentionDraftPreparer interface {
 PrepareMentionCurrent(context.Context,string,string) error
}

func (server *Server) prepareMention(w http.ResponseWriter,r *http.Request) {
 if !server.authenticate(w,r) {return}
 r.Body=http.MaxBytesReader(w,r.Body,4096)
 var body struct { RoomID string `json:"roomid"`;Name string `json:"name"` }
 if decodeJSON(r,&body)!=nil || body.RoomID=="" || body.Name=="" || len(body.Name)>200 {
  writeError(w,400,"room and member name required");return
 }
 server.sendMu.Lock();defer server.sendMu.Unlock()
 // Read-only verification of the already open conversation. No search or Enter.
 if err:=server.verifyDraftCurrent(r.Context(),body.RoomID,body.Name);err!=nil {
  writeError(w,422,"draft conversation/member verification rejected");return
 }
 preparer,ok:=server.sender.(mentionDraftPreparer)
 if !ok {writeError(w,501,"draft preparation unavailable");return}
 if err:=preparer.PrepareMentionCurrent(r.Context(),body.RoomID,body.Name);err!=nil {
  server.logger.Warn("mention draft preparation failed","error",err)
  writeError(w,422,"mention draft preparation failed; nothing submitted for sending");return
 }
 // Deliberately no message ID, no sent receipt and no objective outgoing event.
 writeJSON(w,200,map[string]any{"ret":0,"draft":true,"sent":false,"roomid":body.RoomID})
}

func (server *Server) verifyDraftCurrent(ctx context.Context,room,name string) error {
 url:=os.Getenv("WEBOX_VERIFY_URL");if url=="" {return errors.New("verifier missing")}
 token,err:=os.ReadFile(os.Getenv("WEBOX_VERIFY_TOKEN_FILE"));if err!=nil{return err}
 data,_:=json.Marshal(map[string]any{"roomid":room,"draft":true,"name":name})
 req,err:=http.NewRequestWithContext(ctx,"POST",url,bytes.NewReader(data));if err!=nil{return err}
 req.Header.Set("Content-Type","application/json");req.Header.Set("Authorization","Bearer "+strings.TrimSpace(string(token)))
 client:=http.Client{Timeout:32*time.Second};res,err:=client.Do(req);if err!=nil{return err};defer res.Body.Close()
 var result struct {OK bool `json:"ok"`;Room string `json:"roomid"`}
 if res.StatusCode!=200 || json.NewDecoder(io.LimitReader(res.Body,4096)).Decode(&result)!=nil || !result.OK || result.Room!=room {
  return errors.New("draft not authorized for this visible conversation")
 }
 return nil
}
