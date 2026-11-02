package wechatapi

import (
 "context"
 "encoding/json"
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "testing"
 "github.com/netcat-ai/webox/internal/sender"
)

type atomicTestSender struct{opened bool}
func (s *atomicTestSender) OpenChat(ctx context.Context,target string) error {s.opened=true;return nil}
func (s *atomicTestSender) Send(ctx context.Context,target string,items []sender.Item)(sender.Receipt,error){return sender.Receipt{},nil}

func TestAtomicVerificationCoversImagesAndExactReply(t *testing.T) {
 fake:=&atomicTestSender{}
 validator:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  if !fake.opened {t.Error("verification ran before opening chat")}
  if r.Header.Get("Authorization")!="Bearer test-only" {t.Error("missing verifier authentication")}
  json.NewEncoder(w).Encode(map[string]any{"ok":true,"roomid":"owner","quote_point":map[string]int{"x":600,"y":300}})
 }))
 defer validator.Close()
 token:=filepath.Join(t.TempDir(),"token");os.WriteFile(token,[]byte("test-only"),0600)
 t.Setenv("WEBOX_VERIFY_URL",validator.URL);t.Setenv("WEBOX_VERIFY_TOKEN_FILE",token)
 s:=&Server{sender:fake}
 items:=[]sender.Item{{Kind:"text",Text:"hello",ReplyTo:"77"},{Kind:"image",SharedPath:"outbox/x.png"}}
 if err:=s.verifyOutbound(context.Background(),"owner",items);err!=nil {t.Fatal(err)}
 if !items[0].ChatOpen || !items[1].ChatOpen || items[0].QuotePoint!="600,300" {t.Fatal("verification not applied to entire chain")}
}

func TestVerifierFailureAndWrongEchoFailClosed(t *testing.T) {
 token:=filepath.Join(t.TempDir(),"token");os.WriteFile(token,[]byte("test-only"),0600)
 t.Setenv("WEBOX_VERIFY_TOKEN_FILE",token)
 for _,status:=range []int{200,422} {
  validator:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){w.WriteHeader(status);json.NewEncoder(w).Encode(map[string]any{"ok":true,"roomid":"someone-else"})}))
  t.Setenv("WEBOX_VERIFY_URL",validator.URL)
  items:=[]sender.Item{{Kind:"image",SharedPath:"outbox/x.png"}}
  if (&Server{sender:&atomicTestSender{}}).verifyOutbound(context.Background(),"owner",items)==nil || items[0].ChatOpen {t.Fatal("unverified image accepted")}
  validator.Close()
 }
 t.Setenv("WEBOX_VERIFY_URL","");t.Setenv("WEBOX_REQUIRE_VERIFY","1")
 if (&Server{}).verifyOutbound(context.Background(),"owner",nil)==nil {t.Fatal("missing required verifier accepted")}
}
