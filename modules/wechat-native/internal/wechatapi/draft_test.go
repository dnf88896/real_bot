package wechatapi

import (
 "context"
 "encoding/json"
 "io"
 "log/slog"
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "strings"
 "testing"
 "github.com/netcat-ai/webox/internal/sender"
)

type draftOnlyFake struct { prepared bool; sent bool }
func (f *draftOnlyFake) PrepareMentionCurrent(context.Context,string,string)error{f.prepared=true;return nil}
func (f *draftOnlyFake) Send(context.Context,string,[]sender.Item)(sender.Receipt,error){f.sent=true;return sender.Receipt{},nil}

func TestDraftEndpointNeverInvokesSenderAndHasNoSentReceipt(t *testing.T) {
 token:=filepath.Join(t.TempDir(),"token");os.WriteFile(token,[]byte("test-token"),0600)
 t.Setenv("WEBOX_VERIFY_TOKEN_FILE",token)
 verifier:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  var body map[string]any;json.NewDecoder(r.Body).Decode(&body)
  if body["draft"]!=true {t.Error("draft mode missing")}
  json.NewEncoder(w).Encode(map[string]any{"ok":true,"roomid":"g@chatroom"})
 }))
 defer verifier.Close();t.Setenv("WEBOX_VERIFY_URL",verifier.URL)
 fake:=&draftOnlyFake{}
 server:=&Server{apiToken:"test",sender:fake,logger:slog.New(slog.NewTextHandler(io.Discard,nil))}
 req:=httptest.NewRequest("POST","/ilink/bot/preparemention",strings.NewReader(`{"roomid":"g@chatroom","name":"本人"}`))
 req.Header.Set("AuthorizationType","ilink_bot_token");req.Header.Set("Authorization","Bearer test");req.Header.Set("X-WECHAT-UIN","test")
 response:=httptest.NewRecorder();server.prepareMention(response,req)
 var result map[string]any;json.Unmarshal(response.Body.Bytes(),&result)
 if response.Code!=200 || result["draft"]!=true || result["sent"]!=false || result["client_msg_id"]!=nil || fake.sent || !fake.prepared {t.Fatal("draft endpoint submitted a message or returned a send receipt")}
}
