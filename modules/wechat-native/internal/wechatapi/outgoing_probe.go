package wechatapi

import (
 "net/http"
 "os"
 "strings"
 "github.com/netcat-ai/webox/internal/wechatdb"
)

type outgoingSource interface {
 RoomMessagePositions(string) (wechatdb.RoomMessagePositions,error)
 OutgoingItemsAfter(wechatdb.RoomMessagePositions,string) ([]wechatdb.OutgoingItem,error)
}

func (server *Server) automationStatus(w http.ResponseWriter,r *http.Request) {
 if !server.authenticate(w,r) {return}
 writeJSON(w,http.StatusOK,map[string]any{"ret":0,"read_only":os.Getenv("WEBOX_READ_ONLY")=="1"})
}

func (server *Server) receiptProbe(w http.ResponseWriter,r *http.Request) {
 if !server.authenticate(w,r) {return}
 var body struct {RoomID string `json:"roomid"`; Name string `json:"name"`; Positions wechatdb.RoomMessagePositions `json:"positions"`}
 if err:=decodeJSON(r,&body);err!=nil {writeError(w,400,"invalid receipt request");return}
 if !server.session.IsInitialized() {writeJSON(w,200,sessionUnavailable());return}
 contacts,err:=server.contacts.ContactsByRemark("")
 if err!=nil {writeError(w,503,"contact database unavailable");return}
 matches:=map[string]wechatdb.Contact{}
 for _,row:=range contacts {
  if body.RoomID!="" && row.RoomID==body.RoomID {matches[row.RoomID]=row}
  if body.RoomID=="" && body.Name!="" && (strings.EqualFold(row.Nickname,body.Name)||strings.EqualFold(row.Remark,body.Name)) {matches[row.RoomID]=row}
 }
 if len(matches)!=1 {writeError(w,422,"recipient name missing or ambiguous");return}
 var contact wechatdb.Contact;for _,row:=range matches {contact=row}
 source,ok:=server.rooms.(outgoingSource)
 if !ok {writeError(w,501,"outgoing observer unavailable");return}
 if body.Positions==nil {
  positions,err:=source.RoomMessagePositions(contact.RoomID)
  if err!=nil {writeError(w,503,"outgoing baseline unavailable");return}
  writeJSON(w,200,map[string]any{"ret":0,"contact":contact,"positions":positions});return
 }
 items,err:=source.OutgoingItemsAfter(body.Positions,contact.RoomID)
 if err!=nil {writeError(w,503,"outgoing receipts unavailable");return}
 writeJSON(w,200,map[string]any{"ret":0,"contact":contact,"items":items})
}
