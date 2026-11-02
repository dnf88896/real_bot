package sender

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"os/exec"
 "regexp"
	"strings"
	"time"

	"github.com/netcat-ai/webox/internal/sharedmedia"
	"github.com/netcat-ai/webox/internal/wechat"
	"github.com/netcat-ai/webox/internal/wechatdb"
)

const (
	maxTextLength = 5000
	uiScriptLimit = "38s"
	verifyTries   = 20
	verifyEvery   = 750 * time.Millisecond
)

type Service struct {
	wechat *wechat.State
	media  *sharedmedia.Store
}

type Receipt struct {
	ClientMessageID string
 NativeMessageID string
 ReplyTo string
}

type Item struct {
	Kind       string
	Text       string
	SharedPath string
 ChatOpen bool
 ReplyTo string
 QuotePoint string
}

type preparedItem struct {
	Kind        string
	Text        string
	Path        string
	ContentType string
	Filename    string
	ChatOpen    bool
 ReplyTo string
 QuotePoint string
}

func New(state *wechat.State, media *sharedmedia.Store) *Service {
	return &Service{wechat: state, media: media}
}

// OpenChat brings the recipient conversation to the front and returns. It exists
// so an independent checker can verify the chat title before any text is typed;
// the send path itself can then run with the open step skipped.
func (service *Service) OpenChat(ctx context.Context, target string) error {
	if target == "" || len(target) > 200 {
		return errors.New("recipient is empty or too long")
	}
	recipient, err := service.wechat.ResolveRecipient(target)
	if err != nil {
		return err
	}
	search := base64.StdEncoding.EncodeToString([]byte(recipient.SearchTerm))
	script := uiScriptPrelude()
	script = append(script, openChatScript(search))
	script = append(script, "true", "sleep 0.2")
	if err := runUIScript(ctx, uiScriptLimit, strings.Join(script, "; "), "open chat"); err != nil {
		return err
	}
	return nil
}

func (service *Service) Send(ctx context.Context, target string, items []Item) (Receipt, error) {
	if target == "" || len(target) > 200 {
		return Receipt{}, errors.New("recipient is empty or too long")
	}
	prepared, err := service.prepareItems(target, items)
	if err != nil {
		return Receipt{}, err
	}
	recipient, err := service.wechat.ResolveRecipient(target)
	if err != nil {
		return Receipt{}, err
	}
	positions, err := service.wechat.RoomMessagePositions(recipient.Username)
	if err != nil {
		return Receipt{}, err
	}
	receipt := Receipt{ClientMessageID: randomID()}
	if os.Getenv("WEBOX_UI_SEND_DRY_RUN") == "1" {
		return receipt, nil
	}
	search := base64.StdEncoding.EncodeToString([]byte(recipient.SearchTerm))
	if err := runUIScript(ctx, uiScriptLimit, "export WEBOX_CURRENT_ROOM="+shellQuoteSingle(target)+"; "+sendItemsScript(search, prepared), "send message"); err != nil {
		return Receipt{}, err
	}
	for range verifyTries {
		matched, found, err := service.verifyItems(positions, recipient.Username, prepared)
		if err != nil {
			return Receipt{}, fmt.Errorf("verify sent message in WeChat db: %w", err)
		}
		if found {
            if len(matched)>0 {receipt.NativeMessageID=matched[len(matched)-1].MessageID;receipt.ReplyTo=matched[0].ParentID}
			return receipt, nil
		}
		select {
		case <-ctx.Done():
			return Receipt{}, ctx.Err()
		case <-time.After(verifyEvery):
		}
	}
	return Receipt{}, errors.New("send verification failed: not every item was found in WeChat db")
}

func (service *Service) prepareItems(target string, items []Item) ([]preparedItem, error) {
	if len(items) == 0 {
		return nil, errors.New("message items are empty")
	}
	prepared := make([]preparedItem, 0, len(items))
	for _, item := range items {
		switch item.Kind {
		case "text":
			text := item.Text
			chatOpen := strings.HasPrefix(text, markerOpen)
			if chatOpen {
				text = strings.TrimPrefix(text, markerOpen)
			}
			if strings.TrimSpace(text) == "" || len(text) > maxTextLength {
				return nil, errors.New("text is empty or too long")
			}
			prepared = append(prepared, preparedItem{Kind: "text", Text: text, ChatOpen: chatOpen || item.ChatOpen, ReplyTo: item.ReplyTo, QuotePoint: item.QuotePoint})
		case "image":
			if service.media == nil {
				return nil, errors.New("shared media store is unavailable")
			}
			path, contentType, err := service.media.ResolveImage(target, item.SharedPath)
			if err != nil {
				return nil, err
			}
			prepared = append(prepared, preparedItem{Kind: "image", Path: path, ContentType: contentType, ChatOpen: item.ChatOpen})
		case "file":
			if service.media == nil {
				return nil, errors.New("shared media store is unavailable")
			}
			path, filename, err := service.media.ResolveFile(target, item.SharedPath)
			if err != nil {
				return nil, err
			}
			prepared = append(prepared, preparedItem{Kind: "file", Path: path, Filename: filename, ChatOpen: item.ChatOpen})
		default:
			return nil, fmt.Errorf("unsupported message item kind %q", item.Kind)
		}
	}
	return prepared, nil
}

func (service *Service) verifyItems(positions wechatdb.RoomMessagePositions, username string, items []preparedItem) ([]wechatdb.OutgoingItem, bool, error) {
    sent,err:=service.wechat.OutgoingItemsAfter(positions,username)
    if err!=nil { return nil,false,err }
    matched,ok:=matchItems(sent,items);return matched,ok,nil
}

func containsItems(sent []wechatdb.OutgoingItem, expected []preparedItem) bool {
    _,ok:=matchItems(sent,expected);return ok
}

func matchItems(sent []wechatdb.OutgoingItem, expected []preparedItem) ([]wechatdb.OutgoingItem,bool) {
    used:=make([]bool,len(sent)); matched:=make([]wechatdb.OutgoingItem,0,len(expected))
    for _,item:=range expected {
        found:=-1
        for i,actual:=range sent {
            if used[i] || actual.Kind!=item.Kind { continue }
            if item.Kind=="text" {
                if actual.ParentID!="" && actual.ParentID!=item.ReplyTo {continue}
                _,_,segments:=parseTextPayload(item.Text)
                pattern:="^"
                for _,segment:=range segments {
                    if segment.Mention!="" {pattern+="@"+regexp.QuoteMeta(segment.Mention)+`[\s\x{2005}\x{2004}\x{00a0}]*`} else {pattern+=regexp.QuoteMeta(segment.Text)}
                }
                pattern+="$"
                if ok,_:=regexp.MatchString(pattern,actual.Value); !ok {continue}
            } else if item.Kind=="file" && actual.Value!=item.Filename {continue}
            found=i;break
        }
        if found<0 {return nil,false};used[found]=true;matched=append(matched,sent[found])
    }
    return matched,true
}

// Text payload markers, produced by the independent WeChat sender program from the
// mouth's existing at/reply components. The mouth protocol is untouched: \x01
// delimits a member display name to turn into a native mention, and a leading
// \x02 asks the sender to quote the newest message before composing.
const (
	markerMention = "\x01"
	markerQuote   = "\x02"
	// markerOpen says the conversation was already opened and verified by the
	// independent title checker, so the send path must not search again.
	markerOpen = "\x03"
)

type segment struct {
	Text    string
	Mention string
}

func isPoint(value string) bool {
	parts := strings.Split(value, ",")
	if len(parts) != 2 || len(parts[0]) == 0 || len(parts[0]) > 4 || len(parts[1]) == 0 || len(parts[1]) > 4 {
		return false
	}
	for _, part := range parts {
		for _, ch := range part {
			if ch < '0' || ch > '9' {
				return false
			}
		}
	}
	return true
}

// parseTextPayload reads the sender-side markers. A quote payload may carry the
// bubble point the independent locator measured: "\x02< x>,<y>\x02" before the
// text. Without a point the quote is dropped rather than guessed.
func parseTextPayload(text string) (bool, string, []segment) {
	quote := strings.HasPrefix(text, markerQuote)
	point := ""
	if quote {
		text = strings.TrimPrefix(text, markerQuote)
		if end := strings.Index(text, markerQuote); end > 0 && end <= 12 {
			candidate := text[:end]
			if isPoint(candidate) {
				point = candidate
				text = text[end+len(markerQuote):]
			}
		}
	}
	segments := make([]segment, 0, 4)
	for index, piece := range strings.Split(text, markerMention) {
		if index%2 == 1 {
			if piece != "" {
				segments = append(segments, segment{Mention: piece})
			}
			continue
		}
		if piece != "" {
			segments = append(segments, segment{Text: piece})
		}
	}
	if len(segments) == 0 && text != "" {
		segments = append(segments, segment{Text: text})
	}
	return quote, point, segments
}

func sendItemsScript(searchBase64 string, items []preparedItem) string {
	script := uiScriptPrelude()
	alreadyOpen := len(items) > 0 && items[0].ChatOpen
	if alreadyOpen {
		script = append(script, "guard", "reject_browser", "focus_compose")
	} else {
		script = append(script, openChatScript(searchBase64))
		script = append(script, "focus_compose")
	}
	for _, item := range items {
		switch item.Kind {
		case "text":
			quote, point, segments := parseTextPayload(item.Text)
            if item.ReplyTo!="" && item.QuotePoint!="" {quote=true;point=item.QuotePoint}
			if quote || len(segments) > 1 || (len(segments) == 1 && segments[0].Mention != "") {
                script = append(script,"begin_compose")
				if quote && point != "" {
					// A failed quote must never stop the message: the quote is a
					// nicety, and quote_at closes any menu it opened before giving up.
					script = append(script, "quote_at "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(point))) )
				}
                script=append(script,"focus_compose")
				literal := ""
				for _, segment := range segments {
					if segment.Mention != "" {
                        literal+="@"+segment.Mention
						script = append(script, "mention_member "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(segment.Mention))))
						continue
					}
					script = append(script, "paste_segment "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(segment.Text))))
					literal += segment.Text
				}
				if literal != "" {
					script = append(script, "verify_draft "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(literal))))
				}
			} else {
				script = append(script,
					"paste_text "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(item.Text))),
				)
			}
			script = append(script, "sleep 0.2")
		case "image":
			script = append(script,
				"guard",
				"cleanup_clip",
				"xclip -selection clipboard -target "+shellQuoteSingle(item.ContentType)+" -loops 5 -i "+shellQuoteSingle(item.Path)+" >/dev/null 2>&1 & clip_pid=$!",
				"sleep 0.25",
				"paste_clip",
				"sleep 1",
			)
		case "file":
			fileURL := (&url.URL{Scheme: "file", Path: item.Path}).String() + "\r\n"
			script = append(script,
				"guard",
				"cleanup_clip",
				"printf '%s' "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(fileURL)))+" | base64 -d | xclip -selection clipboard -target text/uri-list -loops 5 -i >/dev/null 2>&1 & clip_pid=$!",
				"sleep 0.25",
				"paste_clip",
				"sleep 1",
			)
		}
		script = append(script,
			"guard",
			"xdotool key --clearmodifiers Return",
			"sleep 0.7",
		)
	}
	script = append(script,
		"true",
		"sleep 0.2",
	)
	return strings.Join(script, "; ")
}

// uiScriptPrelude defines every helper the UI steps rely on. All of it comes
// from what WeChat Linux 4.1.13 actually does on this desktop:
//
//   - the main window is the largest visible window titled 微信/WeChat and it
//     is 880x640; the search box and the logout confirmation are separate
//     windows that share the same X class, so they must be told apart by size;
//   - Ctrl+F opens a 320px wide helper window (empty 320x64, with results
//     320x462), and closing the search with Escape is only safe while that
//     overlay exists;
//   - Escape on the idle main window is the window-close gesture and raises
//     "退出登录后将无法收到新消息，确定退出登录？" (282x162, default button 确定);
//     that modal swallows every key, which used to break the draft check and
//     could log the account out through a stray Return;
//   - therefore Escape is only sent by close_search(), and every step starts
//     with guard(), which cancels the modal by mouse and aborts when it cannot
//     be dismissed.
func uiScriptPrelude() []string {
	return []string{
		"set -e",
		`display="${DISPLAY:-}"`,
		`if [ -z "$display" ]; then for x in /tmp/.X11-unix/X*; do [ -e "$x" ] || continue; display=":${x##*X}"; break; done; fi`,
		`export DISPLAY="${display:-:1}"`,
		`command -v xclip >/dev/null 2>&1 || { echo "xclip not installed" >&2; exit 127; }`,
		`command -v xdotool >/dev/null 2>&1 || { echo "xdotool not installed" >&2; exit 127; }`,
		`clip_pid=""`,
		`cleanup_clip() { if [ -n "${clip_pid:-}" ]; then kill "$clip_pid" 2>/dev/null || true; wait "$clip_pid" 2>/dev/null || true; clip_pid=""; fi; }`,
		`set_clip() { cleanup_clip; printf '%s' "$1" | base64 -d | xclip -selection clipboard -target UTF8_STRING -loops 5 -i >/dev/null 2>&1 & clip_pid=$!; sleep 0.25; }`,
		`paste_clip() { xdotool key --clearmodifiers ctrl+v; for i in $(seq 1 30); do if ! kill -0 "$clip_pid" 2>/dev/null; then wait "$clip_pid" 2>/dev/null || true; clip_pid=""; sleep 0.1; return 0; fi; sleep 0.1; done; echo "wechat did not read clipboard" >&2; return 3; }`,
		`win_list() { xdotool search --onlyvisible --class wechat 2>/dev/null || true; }`,
		`win_size() { box="$(xdotool getwindowgeometry --shell "$1" 2>/dev/null)" || return 1; printf '%s %s' "$(printf '%s\n' "$box" | sed -n 's/^WIDTH=//p')" "$(printf '%s\n' "$box" | sed -n 's/^HEIGHT=//p')"; }`,
		`win_title() { xdotool getwindowname "$1" 2>/dev/null || true; }`,
		`main_window() { picked=""; picked_area=0; for candidate in $(win_list); do case "$(win_title "$candidate")" in 微信|WeChat) ;; *) continue;; esac; size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; [ "$width" -ge 800 ] && [ "$height" -ge 600 ] || continue; [ "$width" -le 900 ] && [ "$height" -le 700 ] || continue; area=$((width*height)); if [ "$area" -gt "$picked_area" ]; then picked="$candidate"; picked_area=$area; fi; done; [ -n "$picked" ] || return 1; printf '%s' "$picked"; }`,
		`browser_window() { for candidate in $(win_list); do size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; if [ "$width" -gt 900 ] || [ "$height" -gt 720 ]; then printf '%s' "$candidate"; return 0; fi; done; return 1; }`,
		`close_browser() { echo "Unknown WeChat window; automatic closing disabled" >&2; return 8; }`,
		`reject_browser() { if [ -n "$(browser_window || true)" ]; then echo "Unverified WeChat window; send aborted without closing any window" >&2; exit 8; fi; }`,
		`overlay_window() { for candidate in $(win_list); do size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; if [ "$width" -ge 300 ] && [ "$width" -le 360 ] && [ "$height" -ge 40 ] && [ "$height" -le 600 ]; then printf '%s' "$candidate"; return 0; fi; done; return 1; }`,
		`logout_modal() { for candidate in $(win_list); do size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; if [ "$width" -ge 260 ] && [ "$width" -le 299 ] && [ "$height" -ge 140 ] && [ "$height" -le 200 ]; then printf '%s' "$candidate"; return 0; fi; done; return 1; }`,
		`cancel_modal() { modal="$(logout_modal || true)"; [ -n "$modal" ] || return 0; xdotool mousemove --window "$modal" 199 109 >/dev/null 2>&1 || true; sleep 0.2; xdotool click 1; sleep 0.6; [ -z "$(logout_modal || true)" ] || { echo "WeChat logout dialog could not be dismissed" >&2; return 6; }; }`,
		`guard() { cancel_modal || exit 6; }`,
		`wait_overlay() { for i in $(seq 1 30); do [ -n "$(overlay_window || true)" ] && return 0; sleep 0.1; done; return 1; }`,
		`wait_overlay_gone() { for i in $(seq 1 40); do [ -z "$(overlay_window || true)" ] && return 0; sleep 0.1; done; return 1; }`,
		`close_search() { [ -n "$(overlay_window || true)" ] || return 0; safe_main="$(main_window)" || { echo "No verified main window for dismissing search" >&2; return 7; }; safe_size="$(win_size "$safe_main")" || return 7; safe_width="${safe_size%% *}"; safe_height="${safe_size##* }"; xdotool mousemove --window "$safe_main" "$((safe_width-100))" "$((safe_height-100))" click 1; sleep 0.4; wait_overlay_gone || { echo "WeChat search overlay would not close safely" >&2; return 7; }; }`,
		`focus_compose() { win="$(main_window)" || { echo "WeChat main window unavailable" >&2; exit 4; }; size="$(win_size "$win")" || { echo "WeChat main window vanished" >&2; exit 4; }; box_width="${size%% *}"; box_height="${size##* }"; [ "$box_width" -ge 800 ] && [ "$box_height" -ge 600 ] || { echo "unexpected WeChat window geometry" >&2; exit 4; }; xdotool mousemove --window "$win" "$((box_width-100))" "$((box_height-100))" >/dev/null 2>&1 || { echo "WeChat main window vanished" >&2; exit 4; }; sleep 0.1; xdotool click --repeat 2 --delay 150 1; sleep 0.2; }`,
		`paste_text() { expected="$1"; want="$(printf '%s' "$expected" | base64 -d | tr -d '\r' | sed -e 's/[[:space:]]*$//' | base64 -w0)"; for attempt in 1 2 3; do guard; focus_compose; xdotool key --clearmodifiers ctrl+a BackSpace; sleep 0.15; set_clip "$expected"; if ! paste_clip; then xdotool key --clearmodifiers ctrl+a BackSpace; sleep 0.4; continue; fi; sleep 0.3; xdotool key --clearmodifiers ctrl+a; set_clip 'c2VudGluZWw='; xdotool key --clearmodifiers ctrl+c; sleep 0.3; copied="$(timeout 3 xclip -selection clipboard -target UTF8_STRING -o 2>/dev/null | tr -d '\r' | sed -e 's/[[:space:]]*$//' | base64 -w0)"; if [ "$copied" = "$want" ]; then xdotool key --clearmodifiers Right; return 0; fi; xdotool key --clearmodifiers ctrl+a BackSpace; sleep 0.3; done; echo "WeChat draft verification failed before send" >&2; return 5; }`,
		`begin_compose() { guard; focus_compose; xdotool key --clearmodifiers ctrl+a BackSpace; sleep 0.15; }`,
		`paste_segment() { guard; set_clip "$1"; paste_clip || { echo "WeChat segment paste did not take" >&2; return 3; }; sleep 0.3; }`,
		`picker_window() { for candidate in $(win_list); do size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; if [ "$width" -ge 120 ] && [ "$width" -le 260 ] && [ "$height" -ge 30 ] && [ "$height" -le 600 ]; then printf '%s' "$candidate"; return 0; fi; done; return 1; }`,
		`wait_picker() { for i in $(seq 1 20); do [ -n "$(picker_window || true)" ] && return 0; sleep 0.1; done; return 1; }`,
        `mention_point() { python3 -c 'import os,json,urllib.request;from pathlib import Path;u=os.environ['"'"'WEBOX_VERIFY_URL'"'"'].rsplit('"'"'/'"'"',1)[0]+'"'"'/mention-picker'"'"';t=Path(os.environ['"'"'WEBOX_VERIFY_TOKEN_FILE'"'"']).read_text().strip();b={'"'"'roomid'"'"':os.environ['"'"'WEBOX_CURRENT_ROOM'"'"'],'"'"'name'"'"':__import__('"'"'sys'"'"').argv[1]};q=urllib.request.Request(u,data=json.dumps(b).encode(),headers={'"'"'Authorization'"'"':'"'"'Bearer '"'"'+t,'"'"'Content-Type'"'"':'"'"'application/json'"'"'});r=json.load(urllib.request.urlopen(q,timeout=27));assert r.get('"'"'ok'"'"') is True;print(int(r['"'"'x'"'"']),int(r['"'"'y'"'"']))' "$(printf '%s' "$1" | base64 -d)"; }`,
        `mention_member() { guard; xdotool key --clearmodifiers ctrl+End; xdotool type --clearmodifiers --delay 80 '@'; wait_picker || { echo "WeChat mention picker did not open" >&2; return 8; }; coords="$(mention_point "$1")" || { focus_compose; return 8; }; mx="${coords%% *}"; my="${coords##* }"; case "$mx$my" in ''|*[!0-9]*) focus_compose; return 8;; esac; xdotool mousemove "$mx" "$my" click 1; sleep 0.4; guard; [ -z "$(picker_window || true)" ] || { echo "WeChat mention picker did not close" >&2; return 8; }; }`,
		`verify_draft() { expected="$(printf '%s' "$1" | base64 -d | tr -d '\r' | sed -e 's/[[:space:]]//g')"; xdotool key --clearmodifiers ctrl+a; set_clip 'c2VudGluZWw='; xdotool key --clearmodifiers ctrl+c; sleep 0.3; copied="$(timeout 3 xclip -selection clipboard -target UTF8_STRING -o 2>/dev/null | tr -d '\r' | sed -e 's/[[:space:]]//g')"; if [ "$copied" = "$expected" ]; then xdotool key --clearmodifiers Right; return 0; fi; echo "WeChat composed draft lost its text before send" >&2; return 5; }`,
		`menu_window() { for candidate in $(win_list); do size="$(win_size "$candidate")" || continue; width="${size%% *}"; height="${size##* }"; case "$width$height" in ''|*[!0-9]*) continue;; esac; if [ "$width" -ge 140 ] && [ "$width" -le 200 ] && [ "$height" -ge 220 ]; then printf '%s' "$candidate"; return 0; fi; done; return 1; }`,
        `quote_menu_point() { python3 -c 'import os,json,urllib.request; from pathlib import Path; u=os.environ['"'"'WEBOX_VERIFY_URL'"'"'].rsplit('"'"'/'"'"',1)[0]+'"'"'/quote-menu'"'"'; t=Path(os.environ['"'"'WEBOX_VERIFY_TOKEN_FILE'"'"']).read_text().strip(); q=urllib.request.Request(u,data=json.dumps({'"'"'roomid'"'"':os.environ['"'"'WEBOX_CURRENT_ROOM'"'"']}).encode(),headers={'"'"'Authorization'"'"':'"'"'Bearer '"'"'+t,'"'"'Content-Type'"'"':'"'"'application/json'"'"'}); r=json.load(urllib.request.urlopen(q,timeout=27)); assert r.get('"'"'ok'"'"') is True; print(int(r['"'"'x'"'"']),int(r['"'"'y'"'"']))' ; }`,
        `quote_at() { guard; win="$(main_window)" || return 9; xy="$(printf '%s' "$1" | base64 -d)"; qx="${xy%%,*}"; qy="${xy##*,}"; case "$qx$qy" in ''|*[!0-9]*) return 9;; esac; size="$(win_size "$win")" || return 9; qw="${size%% *}"; qh="${size##* }"; [ "$qx" -ge 20 ] && [ "$qx" -le $((qw-20)) ] && [ "$qy" -ge 20 ] && [ "$qy" -le $((qh-140)) ] || return 9; xdotool mousemove --window "$win" "$qx" "$qy"; sleep 0.15; xdotool click 3; sleep 0.6; coords="$(quote_menu_point)" || { focus_compose; return 9; }; mx="${coords%% *}"; my="${coords##* }"; case "$mx$my" in ''|*[!0-9]*) focus_compose; return 9;; esac; xdotool mousemove "$mx" "$my" click 1; sleep 0.4; }`,
		`trap cleanup_clip EXIT`,
	}
}

// openChatScript opens the recipient chat from search. It deliberately contains
// No Escape is allowed in the UI script; search is dismissed by a safe click.
// while the search overlay is open - the one state where Escape is harmless.
func openChatScript(queryBase64 string) string {
	return fmt.Sprintf(
		`guard; `+
			`reject_browser; `+
			`close_search; `+
			`main_win="$(main_window)" || { echo "WeChat main window unavailable" >&2; exit 4; }; `+
			`win="$main_win"; xdotool windowactivate "$win"; xdotool windowraise "$win" 2>/dev/null || true; sleep 0.2; `+
			`xdotool key --clearmodifiers ctrl+f; `+
			`if ! wait_overlay; then xdotool key --clearmodifiers ctrl+f; wait_overlay || { echo "WeChat search did not open" >&2; exit 8; }; fi; `+
			`xdotool key --clearmodifiers ctrl+a BackSpace; sleep 0.2; `+
			`set_clip %s; paste_clip; sleep 1.5; `+
			`guard; `+
			`xdotool key --clearmodifiers Return; sleep 0.4; `+
			`reject_browser; `+
			`if ! wait_overlay_gone; then guard; close_search; reject_browser; fi; `+
			`sleep 1.1`,
		shellQuoteSingle(queryBase64),
	)
}

func runUIScript(ctx context.Context, timeout, script, action string) error {
	output, err := exec.CommandContext(ctx, "timeout", timeout, "bash", "-lc", script).CombinedOutput()
	if err == nil {
		return nil
	}
	return fmt.Errorf("%s failed: %w: %s", action, err, strings.TrimSpace(string(output)))
}

func shellQuoteSingle(value string) string {
	return "'" + strings.ReplaceAll(value, "'", `'"'"'`) + "'"
}

func randomID() string {
	value := make([]byte, 16)
	if _, err := rand.Read(value); err != nil {
		return strconvFallbackID()
	}
	return hex.EncodeToString(value)
}

func strconvFallbackID() string {
	return fmt.Sprintf("%032x", time.Now().UnixNano())
}

// PrepareMentionCurrent reuses the native mention implementation but has no
// search, Return key, Send call or database delivery acknowledgement.
func (service *Service) PrepareMentionCurrent(ctx context.Context,target,name string) error {
    script:=uiScriptPrelude()
    script=append(script,"export WEBOX_CURRENT_ROOM="+shellQuoteSingle(target),"guard","begin_compose",
        "mention_member "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte(name))),
        "verify_draft "+shellQuoteSingle(base64.StdEncoding.EncodeToString([]byte("@"+name))))
    return runUIScript(ctx,uiScriptLimit,strings.Join(script,"; "),"prepare mention draft")
}
