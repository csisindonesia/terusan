package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/csis/terusan/services/api/internal/conversations"
)

// The assistant's conversations, addressed by UUID.
//
// The chat route records every turn (see assistant.go); these read them back,
// so a chat reopened from its URL — in another tab, on another machine, after
// the reader signed in again — carries on where it stopped. Absent without an
// application database, where the portal keeps chats in the browser instead.

// viewer is who is asking, for the conversation store: the account, or "" for
// a reader without one.
func (s *Server) viewer(r *http.Request) string {
	if session, err := s.session(r); err == nil {
		return session.User.ID
	}
	return ""
}

func (s *Server) chatsReady(w http.ResponseWriter) bool {
	if s.chats == nil {
		writeError(w, http.StatusNotFound, CodeNotFound,
			"this deployment keeps no conversations",
			"set APP_DB where the API runs; until then the portal keeps chats in the browser")
		return false
	}
	return true
}

// openTurn finds or starts the conversation a question belongs to, records the
// question (or, for a regenerated reply, drops the reply being replaced), and
// returns the conversation and the turns to answer from.
func (s *Server) openTurn(
	w http.ResponseWriter, r *http.Request, body assistantRequest,
) (conversations.Conversation, []assistantMessage, bool) {
	ctx := r.Context()
	viewer := s.viewer(r)
	question := strings.TrimSpace(body.Message)
	if len(question) > assistantMaxMessage {
		question = question[:assistantMaxMessage]
	}

	var chat conversations.Conversation
	var err error
	if body.ConversationID == "" {
		if question == "" || body.Regenerate {
			badRequest(w, "invalid body", "message: a new conversation starts with a question")
			return chat, nil, false
		}
		chat, err = s.chats.Create(ctx, viewer, question)
		if err != nil {
			internalError(w, s.log, "start conversation", err)
			return chat, nil, false
		}
		chat.Messages = []conversations.Message{}
	} else {
		chat, err = s.chats.Get(ctx, body.ConversationID, viewer)
		if errors.Is(err, conversations.ErrNotFound) {
			notFound(w, "no such conversation", body.ConversationID)
			return chat, nil, false
		}
		if err != nil {
			internalError(w, s.log, "open conversation", err)
			return chat, nil, false
		}
	}

	if body.Regenerate {
		// The last question stays; everything after it goes, and is answered
		// again.
		last := -1
		for _, m := range chat.Messages {
			if m.Role == "user" {
				last = m.Seq
			}
		}
		if last < 0 {
			badRequest(w, "invalid body", "regenerate: there is no question to answer again")
			return chat, nil, false
		}
		if err := s.chats.TruncateAfter(ctx, chat.ID, last); err != nil {
			internalError(w, s.log, "regenerate", err)
			return chat, nil, false
		}
		chat.Messages = chat.Messages[:indexAfter(chat.Messages, last)]
	} else {
		if question == "" {
			badRequest(w, "invalid body", "message: a question is needed")
			return chat, nil, false
		}
		appended, err := s.chats.Append(ctx, chat.ID, conversations.Message{Role: "user", Content: question})
		if err != nil {
			badRequest(w, "conversation full", err.Error())
			return chat, nil, false
		}
		chat.Messages = append(chat.Messages, appended)
	}

	turns := make([]assistantMessage, 0, len(chat.Messages))
	for _, m := range chat.Messages {
		// A reply that never started is not a turn to answer from.
		if m.Role == "assistant" && m.Content == "" {
			continue
		}
		turns = append(turns, assistantMessage{Role: m.Role, Content: m.Content})
	}
	return chat, turns, true
}

// indexAfter is the slice index just past the message numbered seq.
func indexAfter(messages []conversations.Message, seq int) int {
	for i, m := range messages {
		if m.Seq == seq {
			return i + 1
		}
	}
	return len(messages)
}

func (s *Server) handleConversations(w http.ResponseWriter, r *http.Request) {
	if !s.chatsReady(w) {
		return
	}
	list, err := s.chats.List(r.Context(), s.viewer(r))
	if err != nil {
		internalError(w, s.log, "list conversations", err)
		return
	}
	writeData(w, list, &Meta{Total: int64(len(list))})
}

func (s *Server) handleConversation(w http.ResponseWriter, r *http.Request) {
	if !s.chatsReady(w) {
		return
	}
	chat, err := s.chats.Get(r.Context(), r.PathValue("id"), s.viewer(r))
	if errors.Is(err, conversations.ErrNotFound) {
		notFound(w, "no such conversation", r.PathValue("id"))
		return
	}
	if err != nil {
		internalError(w, s.log, "open conversation", err)
		return
	}
	writeData(w, chat, nil)
}

func (s *Server) handleDeleteConversation(w http.ResponseWriter, r *http.Request) {
	if !s.chatsReady(w) {
		return
	}
	err := s.chats.Delete(r.Context(), r.PathValue("id"), s.viewer(r))
	if errors.Is(err, conversations.ErrNotFound) {
		notFound(w, "no such conversation", r.PathValue("id"))
		return
	}
	if err != nil {
		internalError(w, s.log, "delete conversation", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleSetMessageCollection records the collection a reply was saved into,
// so the "Open collection" link is there when the chat is reopened.
func (s *Server) handleSetMessageCollection(w http.ResponseWriter, r *http.Request) {
	if !s.chatsReady(w) {
		return
	}
	seq, err := strconv.Atoi(r.PathValue("seq"))
	if err != nil || seq < 0 {
		badRequest(w, "invalid parameter", "seq: is not a message number")
		return
	}
	var body struct {
		CollectionID string `json:"collection_id"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 4<<10)).Decode(&body); err != nil {
		badRequest(w, "invalid body", err.Error())
		return
	}
	if len(body.CollectionID) > 200 {
		badRequest(w, "invalid body", "collection_id: too long")
		return
	}
	err = s.chats.SetCollection(r.Context(), r.PathValue("id"), s.viewer(r), seq, body.CollectionID)
	if errors.Is(err, conversations.ErrNotFound) {
		notFound(w, "no such message", r.PathValue("id")+"/"+r.PathValue("seq"))
		return
	}
	if err != nil {
		internalError(w, s.log, "record collection", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
