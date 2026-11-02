package wechatapi

import (
 "bytes"
 "context"
 "encoding/json"
 "errors"
 "fmt"
 "io"
 "net/http"
 "os"
 "strings"
 "time"
 "github.com/netcat-ai/webox/internal/sender"
)

// Called with sendMu held. Open, verify and send share one UI transaction.
// The host performs read-only screenshot/OCR; only this transport operates UI.
func (server *Server) verifyOutbound(ctx context.Context,target string,items []sender.Item) error {
 url:=os.Getenv("WEBOX_VERIFY_URL")
 if url=="" {
  if os.Getenv("WEBOX_REQUIRE_VERIFY")=="1" {return errors.New("required verifier not configured")}
  return nil
 }
 opener,ok:=server.sender.(chatOpener)
 if !ok {return errors.New("chat opener unavailable")}
 if err:=opener.OpenChat(ctx,target);err!=nil{return err}
 token,err:=os.ReadFile(os.Getenv("WEBOX_VERIFY_TOKEN_FILE"))
 if err!=nil{return errors.New("private verifier credential unavailable")}
 replyTo:=""
 for _,item:=range items {if item.ReplyTo!="" {replyTo=item.ReplyTo;break}}
 body,_:=json.Marshal(map[string]string{"roomid":target,"reply_to":replyTo})
 request,err:=http.NewRequestWithContext(ctx,"POST",url,bytes.NewReader(body))
 if err!=nil{return err}
 request.Header.Set("Content-Type","application/json")
 request.Header.Set("Authorization","Bearer "+strings.TrimSpace(string(token)))
 client:=http.Client{Timeout:32*time.Second}
 response,err:=client.Do(request)
 if err!=nil{return errors.New("private verifier unavailable")}
 defer response.Body.Close()
 if response.StatusCode!=200{return errors.New("private verifier rejected conversation")}
 var result struct {
  OK bool `json:"ok"`
  RoomID string `json:"roomid"`
  Point *struct {X int `json:"x"`;Y int `json:"y"`} `json:"quote_point"`
 }
 if err:=json.NewDecoder(io.LimitReader(response.Body,4096)).Decode(&result);err!=nil{return err}
 if !result.OK || result.RoomID!=target {return errors.New("verifier identity mismatch")}
 for i:=range items {
  items[i].ChatOpen=true
  if items[i].ReplyTo!="" && result.Point!=nil {
   items[i].QuotePoint=fmt.Sprintf("%d,%d",result.Point.X,result.Point.Y)
  }
 }
 return nil
}
