package sender

import (
 "testing"
 "github.com/netcat-ai/webox/internal/wechatdb"
)

func TestNativeMentionReceiptUsesVisibleText(t *testing.T) {
 expected:=[]preparedItem{{Kind:"text",Text:"收到"+markerMention+"张三"+markerMention+"，谢谢"}}
 actual:=[]wechatdb.OutgoingItem{{Kind:"text",Value:"收到@张三\u2005，谢谢",MessageID:"100"}}
 matched,ok:=matchItems(actual,expected)
 if !ok || matched[0].MessageID!="100" {t.Fatal("native mention incorrectly reported unsent")}
 actual[0].Value="收到@李四\u2005，谢谢"
 if containsItems(actual,expected) {t.Fatal("wrong mentioned person accepted")}
}

func TestQuotedReceiptRequiresExactParentAndPreservesNativeID(t *testing.T) {
 expected:=[]preparedItem{{Kind:"text",Text:"原文🙂",ReplyTo:"77"}}
 actual:=[]wechatdb.OutgoingItem{{Kind:"text",Value:"原文🙂",ParentID:"77",MessageID:"100"}}
 matched,ok:=matchItems(actual,expected)
 if !ok || matched[0].ParentID!="77" || matched[0].MessageID!="100" {t.Fatal("quote receipt lost identity")}
 actual[0].ParentID="78"
 if containsItems(actual,expected) {t.Fatal("wrong quoted message accepted")}
 expected[0].ReplyTo=""
 if containsItems(actual,expected) {t.Fatal("stale quote accepted as plain text")}
 actual[0].ParentID=""
 if !containsItems(actual,expected) {t.Fatal("plain text no longer confirmed")}
}
