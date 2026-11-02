package sender

import (
	"strings"
	"testing"

	"github.com/netcat-ai/webox/internal/wechatdb"
)

func TestShellQuoteSingle(t *testing.T) {
	if got := shellQuoteSingle("a'b"); got != `'a'"'"'b'` {
		t.Fatalf("unexpected quote: %s", got)
	}
}

func TestOpenChatUsesFirstSearchResultWithoutCoordinateFocus(t *testing.T) {
	script := openChatScript("query")
	if !strings.Contains(script, "set_clip 'query'; paste_clip; sleep 1.5") ||
		!strings.Contains(script, "key --clearmodifiers Return; sleep 0.4") {
		t.Fatalf("unexpected script: %s", script)
	}
	for _, forbidden := range []string{"Down", "getwindowgeometry", "mousemove", "click"} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("script contains %q", forbidden)
		}
	}
}

// The production outage was caused by "Escape" on the idle main window, which
// this WeChat build treats as "close the window" and answers with the
// logout-confirmation modal. Opening a chat must never press it.
func TestOpenChatNeverPressesEscapeItself(t *testing.T) {
	if strings.Contains(openChatScript("query"), "Escape") {
		t.Fatal("openChatScript must not press Escape; only close_search may")
	}
}

func TestNoEscapeOrWindowCloseInGeneratedPrelude(t *testing.T) {
 prelude := strings.Join(uiScriptPrelude(), "; ")
 for _, forbidden := range []string{"Escape", "windowclose", "windowkill", "alt+F4", "width-30"} {
  if strings.Contains(prelude,forbidden) {t.Fatalf("unsafe window-closing action %s",forbidden)}
 }
 start:=strings.Index(prelude,"close_search() {"); end:=strings.Index(prelude[start:],"focus_compose() {")
 body:=prelude[start:start+end]
 for _,required:=range []string{"overlay_window","main_window","safe_width-100","safe_height-100","wait_overlay_gone"} {
  if !strings.Contains(body,required) {t.Fatalf("safe search dismissal missing %s",required)}
 }
 start=strings.Index(prelude,"reject_browser() {");end=strings.Index(prelude[start:],"overlay_window() {")
 body=prelude[start:start+end]
 if strings.Contains(body,"xdotool") || strings.Contains(body,"close_browser;") {t.Fatal("unknown windows must only abort, not close")}
}

func TestPreludeDistinguishesModalAndOverlayBySize(t *testing.T) {
	prelude := strings.Join(uiScriptPrelude(), "; ")
	for _, required := range []string{"logout_modal()", "overlay_window()", "cancel_modal()", "guard()", "main_window()"} {
		if !strings.Contains(prelude, required) {
			t.Fatalf("prelude does not define %q", required)
		}
	}
	if !strings.Contains(prelude, "$width\" -ge 260") || !strings.Contains(prelude, "$width\" -le 299") {
		t.Fatal("logout modal must be matched by its 260-299px width band")
	}
	if !strings.Contains(prelude, "$width\" -ge 300") || !strings.Contains(prelude, "$width\" -le 360") {
		t.Fatal("search overlay must be matched by its 300-360px width band")
	}
}

func TestSendReturnsToContacts(t *testing.T) {
	script := sendItemsScript("target", []preparedItem{{Kind: "text", Text: "message"}})
	if strings.Contains(script, "key --clearmodifiers ctrl+2") {
		t.Fatal("send must leave the active chat open")
	}
}

func TestSendItemsSendsEveryItemSeparately(t *testing.T) {
	script := sendItemsScript("target", []preparedItem{
		{Kind: "text", Text: "message"},
		{Kind: "image", Path: "/tmp/image.png", ContentType: "image/png"},
		{Kind: "file", Path: "/tmp/report.pdf", Filename: "report.pdf"},
	})
	for _, required := range []string{
		"paste_text 'bWVzc2FnZQ=='",
		"xclip -selection clipboard -target 'image/png' -loops 5 -i '/tmp/image.png'",
		"xclip -selection clipboard -target text/uri-list -loops 5 -i",
		"paste_clip", "key --clearmodifiers Return",
	} {
		if !strings.Contains(script, required) {
			t.Fatalf("script does not contain %q: %s", required, script)
		}
	}
	if strings.Count(script, "xdotool key --clearmodifiers Return; sleep 0.7") != 3 {
		t.Fatalf("each item was not submitted separately: %s", script)
	}
	if strings.Count(script, "guard; xdotool key --clearmodifiers Return; sleep 0.7") != 3 {
		t.Fatalf("every submitted item must be guarded against the logout modal: %s", script)
	}
	for _, forbidden := range []string{"ctrl+l", "file_x", "send_x"} {
		if strings.Contains(script, forbidden) {
			t.Fatalf("script contains media-specific coordinate action %q", forbidden)
		}
	}
}

func TestDraftVerificationNormalisesWhitespace(t *testing.T) {
	script := sendItemsScript("target", []preparedItem{{Kind: "text", Text: "message"}})
	if !strings.Contains(script, `tr -d '\r'`) || !strings.Contains(script, `s/[[:space:]]*$//`) {
		t.Fatal("draft verification must normalise CR and trailing whitespace")
	}
	if strings.Contains(script, "paste_clip || true") {
		t.Fatal("a failed paste must not be ignored")
	}
}

func TestContainsItemsCountsDuplicates(t *testing.T) {
	expected := []preparedItem{
		{Kind: "image"},
		{Kind: "image"},
		{Kind: "file", Filename: "report.txt"},
		{Kind: "file", Filename: "report.txt"},
	}
	sent := []wechatdb.OutgoingItem{
		{Kind: "image"},
		{Kind: "file", Value: "report.txt"},
	}
	if containsItems(sent, expected) {
		t.Fatal("one outgoing item satisfied duplicate expected items")
	}
	sent = append(sent,
		wechatdb.OutgoingItem{Kind: "image"},
		wechatdb.OutgoingItem{Kind: "file", Value: "report.txt"},
	)
	if !containsItems(sent, expected) {
		t.Fatal("matching duplicate outgoing items were not found")
	}
}

func TestSearchNeverTypesIntoTheBuiltInBrowser(t *testing.T) {
	script := openChatScript("query")
	if !strings.Contains(script, "reject_browser") {
		t.Fatal("opening a chat must reject the built-in browser window")
	}
	prelude := strings.Join(uiScriptPrelude(), "; ")
	for _, required := range []string{"browser_window()", "close_browser()", "reject_browser()"} {
		if !strings.Contains(prelude, required) {
			t.Fatalf("prelude does not define %q", required)
		}
	}
	if !strings.Contains(prelude, `"$width" -le 900`) {
		t.Fatal("main window must exclude the 922px built-in browser")
	}
}

func TestParseTextPayloadMarksMentions(t *testing.T) {
	quote, point, segments := parseTextPayload("收到" + markerMention + "ExampleFriend" + markerMention + "，我看看")
	if quote || point != "" {
		t.Fatal("payload must not request a quote")
	}
	expected := []segment{{Text: "收到"}, {Mention: "ExampleFriend"}, {Text: "，我看看"}}
	if len(segments) != len(expected) {
		t.Fatalf("unexpected segments: %#v", segments)
	}
	for index, want := range expected {
		if segments[index] != want {
			t.Fatalf("segment %d = %#v, want %#v", index, segments[index], want)
		}
	}
}

func TestParseTextPayloadMarksQuote(t *testing.T) {
	quote, point, segments := parseTextPayload(markerQuote + "回复正文")
	if !quote {
		t.Fatal("leading quote marker was not detected")
	}
	if point != "" {
		t.Fatalf("a quote without coordinates must not invent one: %q", point)
	}
	if len(segments) != 1 || segments[0].Text != "回复正文" {
		t.Fatalf("unexpected segments: %#v", segments)
	}
}

func TestParseTextPayloadReadsQuotePoint(t *testing.T) {
	quote, point, segments := parseTextPayload(markerQuote + "734,469" + markerQuote + "回复正文")
	if !quote || point != "734,469" {
		t.Fatalf("quote point not parsed: %v %q", quote, point)
	}
	if len(segments) != 1 || segments[0].Text != "回复正文" {
		t.Fatalf("unexpected segments: %#v", segments)
	}
	// Anything that is not a coordinate pair must stay part of the text.
	quote, point, segments = parseTextPayload(markerQuote + "abc" + markerQuote + "正文")
	if point != "" || len(segments) != 1 || segments[0].Text != "abc"+markerQuote+"正文" {
		t.Fatalf("non-numeric prefix must not become a point: %q %#v", point, segments)
	}
}

func TestMentionAndQuoteScriptsUseUiHelpers(t *testing.T) {
	script := sendItemsScript("target", []preparedItem{{Kind: "text", Text: markerQuote + "700,460" + markerQuote + "开始" + markerMention + "ExampleFriend" + markerMention + "结束"}})
	for _, required := range []string{"quote_at", "begin_compose", "mention_member", "paste_segment", "verify_draft"} {
		if !strings.Contains(script, required) {
			t.Fatalf("script does not call %q: %s", required, script)
		}
	}
	if strings.Contains(script, "paste_text '") {
		t.Fatal("marked payload must not go through the single-paste path")
	}
	prelude := strings.Join(uiScriptPrelude(), "; ")
	for _, required := range []string{"picker_window()", "quote_at()", "menu_window()", "verify_draft()"} {
		if !strings.Contains(prelude, required) {
			t.Fatalf("prelude does not define %q", required)
		}
	}
	if !strings.Contains(prelude, "logout_modal()") || !strings.Contains(prelude, "guard()") {
		t.Fatal("prelude must keep the logout-modal guard")
	}
}
