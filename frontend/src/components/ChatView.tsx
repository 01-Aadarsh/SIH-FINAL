"use client";

import { useRef, useState } from "react";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import {
  ApiError,
  ClientTimeoutError,
  query,
  queryStream,
  transcribeAudio,
} from "@/lib/api";
import type {
  ChatTurn,
  Citation,
  ConversationMessage,
  Jurisdiction,
  StreamDoneData,
} from "@/lib/types";
import { Header } from "./Header";
import { ChatMessageBubble } from "./ChatMessageBubble";
import { LoadingState } from "./LoadingState";
import { SourceViewer } from "./SourceViewer";

let idCounter = 0;
const nextId = () => `msg-${++idCounter}-${Date.now()}`;

/** MediaRecorder's supported mimeTypes vary by browser; pick the first one
 * this backend actually accepts (.webm, .mp4 → served as .m4a — see
 * backend/api/asr.py::ALLOWED_EXTENSIONS) rather than trusting the
 * browser's undocumented default, which can be a container this backend
 * would reject outright. */
function pickRecorderMimeType(): { mimeType: string | undefined; extension: string } {
  const candidates: Array<{ mimeType: string; extension: string }> = [
    { mimeType: "audio/webm", extension: "webm" },
    { mimeType: "audio/mp4", extension: "m4a" },
    { mimeType: "audio/ogg", extension: "webm" }, // backend has no .ogg — closest accepted container
  ];
  for (const candidate of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(candidate.mimeType)) {
      return candidate;
    }
  }
  // No mimeType hint supported the browser will report — record with the
  // browser's own default and hope it's one of the four accepted
  // extensions; transcribeAudio() surfaces a clear 415 from the backend
  // if not, rather than silently failing here.
  return { mimeType: undefined, extension: "webm" };
}

export function ChatView({
  jurisdiction,
  category,
  language,
  onChangeContext,
}: {
  jurisdiction: Jurisdiction;
  category: string | null;
  language: string;
  onChangeContext: () => void;
}) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);

  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);

  // Off by default: /query/stream (the fast path below) never returns
  // audio (docs/API_CONTRACT.md), so enabling this switches a turn to the
  // slower non-streaming /query with synthesize_audio:true instead of
  // silently doing nothing.
  const [autoSpeak, setAutoSpeak] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioPlayerRef = useRef<HTMLAudioElement>(null);

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    });
  };

  function applyDoneData(messageId: string, data: StreamDoneData) {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === messageId
          ? {
              ...m,
              content: data.answer,
              citations: data.citations,
              flags: data.flags,
              formulation_category: data.formulation_category,
              confidence_score: data.confidence_score,
              related_provisions: data.related_provisions,
              needs_clarification: data.needs_clarification,
              clarifying_questions: data.clarifying_questions,
              actionable_forms: data.actionable_forms,
              pending: false,
            }
          : m
      )
    );
  }

  async function handleSend(overrideQuestion?: string) {
    const question = (overrideQuestion ?? input).trim();
    if (!question || sending) return;

    const history: ChatTurn[] = messages
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", content: question },
    ]);
    setInput("");
    setSending(true);
    scrollToBottom();

    // The backend has no dedicated "category" field — formulation_category
    // is always triaged server-side from the question text itself (see
    // graph/formulation.py). Folding the intake screen's category pick in
    // as a natural-language hint is what actually makes that UI control
    // do something real against this backend, instead of being sent to a
    // field that doesn't exist and silently ignored. The user's own typed
    // question (not this augmented version) is what's shown in their chat
    // bubble and sent back as `history` on later turns.
    const augmentedQuestion = category
      ? `${question} (regarding a ${category.toLowerCase()} formulation)`
      : question;

    const assistantId = nextId();
    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "", pending: true },
    ]);

    // /query/stream never returns audio (docs/API_CONTRACT.md) — with
    // autoSpeak on, go straight to the non-streaming endpoint so there's
    // actually audio to play, instead of streaming text and then finding
    // out at the end there's nothing to speak.
    if (autoSpeak) {
      try {
        const res = await query({
          question: augmentedQuestion,
          history,
          jurisdiction,
          language,
          synthesize_audio: true,
        });
        applyDoneData(assistantId, res);
        if (res.audio_base64 && audioPlayerRef.current) {
          audioPlayerRef.current.src = `data:audio/wav;base64,${res.audio_base64}`;
          void audioPlayerRef.current.play();
        }
      } catch (err) {
        const message =
          err instanceof ApiError || err instanceof ClientTimeoutError
            ? err.message
            : "Something went wrong talking to the backend.";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: "", error: message, pending: false }
              : m
          )
        );
      } finally {
        setSending(false);
        scrollToBottom();
      }
      return;
    }

    try {
      await queryStream(
        { question: augmentedQuestion, history, jurisdiction, language },
        {
          onToken: (text) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + text } : m
              )
            );
            scrollToBottom();
          },
          onDone: (data) => applyDoneData(assistantId, data),
        }
      );
    } catch {
      // Stream dropped (network hiccup, server restart mid-response) or
      // never connected at all — fall back to the plain, non-streaming
      // endpoint and replace whatever partial text arrived with the real,
      // complete answer. Silent about *why* it fell back: the end result
      // (a correct, complete answer) is what matters to the user, not the
      // transport that produced it.
      try {
        const res = await query({
          question: augmentedQuestion,
          history,
          jurisdiction,
          language,
        });
        applyDoneData(assistantId, res);
      } catch (err) {
        const message =
          err instanceof ApiError || err instanceof ClientTimeoutError
            ? err.message
            : "Something went wrong talking to the backend.";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: "", error: message, pending: false }
              : m
          )
        );
      }
    } finally {
      setSending(false);
      scrollToBottom();
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }

  async function startRecording() {
    setMicError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const { mimeType, extension } = pickRecorderMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      audioChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;

        const blob = new Blob(audioChunksRef.current, {
          type: mimeType ?? recorder.mimeType,
        });
        audioChunksRef.current = [];

        if (blob.size === 0) {
          setMicError("No audio was captured — try holding the mic button longer.");
          return;
        }

        setTranscribing(true);
        try {
          const { transcript } = await transcribeAudio(
            blob,
            `voice-input.${extension}`,
            language
          );
          if (transcript.trim()) {
            setInput(transcript);
            await handleSend(transcript);
          } else {
            setMicError("Couldn't make out any speech in that recording — try again.");
          }
        } catch (err) {
          setMicError(
            err instanceof ApiError
              ? `Transcription failed: ${err.message}`
              : "Transcription failed — check your connection and try again."
          );
        } finally {
          setTranscribing(false);
        }
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setMicError(
          "Microphone access was denied — allow it in your browser's site settings to ask a question by voice."
        );
      } else if (name === "NotFoundError") {
        setMicError("No microphone was found on this device.");
      } else {
        setMicError("Could not start recording — your browser may not support this.");
      }
    }
  }

  function toggleMic() {
    if (recording) {
      stopRecording();
    } else {
      void startRecording();
    }
  }

  return (
    <div className="flex h-screen flex-col bg-paper">
      {/* Hidden — played programmatically when autoSpeak's query() response
       * carries audio_base64. No visible controls: the speaker toggle above
       * is the UI for this. */}
      <audio ref={audioPlayerRef} className="hidden" />
      <Header
        jurisdiction={jurisdiction}
        category={category}
        onChangeContext={onChangeContext}
      />

      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 flex-1 flex-col">
          <div
            ref={scrollRef}
            className="flex-1 space-y-4 overflow-y-auto px-4 py-6 sm:px-8"
          >
            {messages.length === 0 && (
              <div className="mx-auto max-w-md rounded-2xl border border-dashed border-clay-200 p-6 text-center text-sm text-ink/50">
                Ask about IP, ABS, or regulatory posture for an Ayurvedic
                formulation — by typing or by voice — the answer will cite
                exactly which document and page it came from, or say
                plainly that it couldn&apos;t find one.
              </div>
            )}
            {messages
              // A pending assistant message with no content yet (streaming
              // hasn't yielded its first token) has nothing to show —
              // LoadingState fills that gap instead of an empty bubble
              // sitting next to it.
              .filter((m) => !(m.pending && !m.content))
              .map((m) => (
                <ChatMessageBubble
                  key={m.id}
                  message={m}
                  onViewCitation={setActiveCitation}
                />
              ))}
            {sending && !messages.some((m) => m.pending && m.content) && (
              <LoadingState />
            )}
          </div>

          <div className="border-t border-clay-200 bg-white px-4 py-3 sm:px-8">
            {transcribing && (
              <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-forest-600">
                <span className="h-1.5 w-1.5 animate-pulseSoft rounded-full bg-forest-500" />{" "}
                Transcribing via Sarvam Saaras...
              </p>
            )}
            {micError && (
              <p className="mb-1.5 text-xs font-medium text-red-600">{micError}</p>
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void handleSend();
              }}
              className="flex items-end gap-2"
            >
              <button
                type="button"
                onClick={() => setAutoSpeak((v) => !v)}
                aria-pressed={autoSpeak}
                aria-label={autoSpeak ? "Voice replies on" : "Voice replies off"}
                title={
                  autoSpeak
                    ? "Voice replies on — answers will be spoken (slower, uses the non-streaming endpoint)"
                    : "Voice replies off — tap to have answers spoken aloud in the selected language"
                }
                className={`shrink-0 rounded-2xl border px-3 py-2.5 transition ${
                  autoSpeak
                    ? "border-forest-300 bg-forest-50 text-forest-700"
                    : "border-clay-200 text-ink/60 hover:border-forest-300 hover:bg-forest-50 hover:text-forest-700"
                }`}
              >
                {autoSpeak ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
              </button>
              <button
                type="button"
                onClick={toggleMic}
                disabled={sending || transcribing}
                aria-pressed={recording}
                aria-label={recording ? "Stop recording" : "Ask by voice"}
                title={recording ? "Stop recording" : "Ask by voice"}
                className={`shrink-0 rounded-2xl border px-3 py-2.5 transition disabled:cursor-not-allowed disabled:opacity-40 ${
                  recording
                    ? "animate-pulseSoft border-red-300 bg-red-50 text-red-600"
                    : "border-clay-200 text-ink/60 hover:border-forest-300 hover:bg-forest-50 hover:text-forest-700"
                }`}
              >
                {recording ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void handleSend();
                  }
                }}
                rows={1}
                placeholder={recording ? "Listening..." : "Ask a question, or tap the mic..."}
                className="max-h-40 flex-1 resize-none rounded-2xl border border-clay-200 bg-paper px-4 py-2.5 text-sm text-ink outline-none focus:border-forest-500"
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                className="shrink-0 rounded-2xl bg-forest-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </form>
            <p className="mt-1.5 text-center text-[11px] text-ink/35">
              Answers can take up to ~60s — grounded, cited responses are
              slower than a guess.
            </p>
          </div>
        </div>

        <aside className="hidden w-[420px] shrink-0 border-l border-clay-200 bg-white lg:block">
          <SourceViewer
            citation={activeCitation}
            onClose={() => setActiveCitation(null)}
          />
        </aside>
      </div>

      {activeCitation && (
        <div className="fixed inset-0 z-20 flex flex-col bg-white lg:hidden">
          <SourceViewer
            citation={activeCitation}
            onClose={() => setActiveCitation(null)}
          />
        </div>
      )}
    </div>
  );
}
