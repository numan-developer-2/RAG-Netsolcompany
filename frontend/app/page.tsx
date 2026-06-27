"use client";

import {
  Activity,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ExternalLink,
  FileText,
  Globe2,
  History,
  Menu,
  MessageSquare,
  PanelLeftClose,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X
} from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_BASE = "/api/rag";

type Persona = "general" | "executive" | "developer" | "sales";

type Health = {
  status: string;
  web_chunks: number;
  pdf_chunks: number;
  metadata_records: number;
  bm25_ready: boolean;
  embedding_provider: string;
  model: string;
};

type RagResult = {
  answer: string;
  sources: string[];
  confidence: number;
  confidence_label: string;
  persona: string;
  intent: string;
  route: string;
  chunks_retrieved: number;
  chunks_used: number;
  answer_type: string;
  verified: string;
  processing_time: number;
};

type Message = {
  id: string;
  query: string;
  answer: string;
  result?: RagResult;
  failed?: boolean;
};

const personas: Array<{ value: Persona; label: string }> = [
  { value: "general", label: "General" },
  { value: "executive", label: "Executive" },
  { value: "developer", label: "Developer" },
  { value: "sales", label: "Sales" }
];

const suggestions = [
  { icon: BookOpen, label: "Products", query: "What is LeasePak and who is it designed for?" },
  { icon: Search, label: "Compare", query: "Compare LeasePak with NFS Ascent using available sources." },
  { icon: FileText, label: "Financials", query: "Summarize NETSOL's latest available financial performance." }
];

const stageLabels: Record<string, string> = {
  analyzing: "Understanding your question",
  streaming: "Reviewing retrieved evidence",
  token: "Writing a grounded response",
  complete: "Response ready"
};

const springTransition = { type: "spring", stiffness: 260, damping: 28 } as const;

function formatNumber(value?: number) {
  return typeof value === "number" ? value.toLocaleString() : "--";
}

function sourceDetails(source: string) {
  const markdownUrl = source.match(/\[[^\]]+\]\((https?:\/\/[^)]+)\)/)?.[1];
  const plainUrl = source.match(/https?:\/\/[^\s\])]+/)?.[0];
  const url = markdownUrl ?? plainUrl;
  const isPdf = /pdf/i.test(source);

  if (url) {
    try {
      const parsed = new URL(url);
      const pathName = decodeURIComponent(parsed.pathname).split("/").filter(Boolean).pop();
      return {
        href: url,
        title: pathName && pathName.length < 90 ? pathName.replace(/[-_]/g, " ") : parsed.hostname,
        detail: parsed.hostname,
        isPdf
      };
    } catch {
      return { href: url, title: url, detail: "Web source", isPdf };
    }
  }

  const cleaned = source
    .replace(/^PDF:\s*/i, "")
    .replace(/:([0-9]+)$/, ", page $1")
    .replace(/_/g, " ");
  return { href: undefined, title: cleaned, detail: isPdf ? "PDF document" : "Retrieved source", isPdf };
}

function verificationCopy(result?: RagResult) {
  if (!result) return { label: "Preparing", tone: "neutral" };
  if (result.verified === "PASS") return { label: "Grounded", tone: "success" };
  if (result.verified === "PARTIAL") return { label: "Review advised", tone: "warning" };
  return { label: "Limited support", tone: "danger" };
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [persona, setPersona] = useState<Persona>("general");
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState("");
  const [streamText, setStreamText] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const answerEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeMessage = useMemo(
    () => messages.find((message) => message.id === activeId) ?? messages[messages.length - 1],
    [activeId, messages]
  );

  const chatHistory = useMemo(
    () =>
      messages
        .flatMap((message) => [
          { role: "user", content: message.query },
          { role: "assistant", content: message.answer }
        ])
        .slice(-6),
    [messages]
  );

  async function loadHealth() {
    try {
      setHealthError("");
      const response = await fetch(`${API_BASE}/health`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setHealth(await response.json());
    } catch (error) {
      setHealth(null);
      setHealthError(error instanceof Error ? error.message : "Backend unavailable");
    }
  }

  useEffect(() => {
    loadHealth();
  }, []);

  useEffect(() => {
    if (streamText || activeMessage) answerEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [streamText, activeMessage]);

  useEffect(() => {
    document.body.classList.toggle("nav-locked", mobileNavOpen);
    return () => document.body.classList.remove("nav-locked");
  }, [mobileNavOpen]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 150)}px`;
  }, [query]);

  useEffect(() => {
    function closeOnEscape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") setMobileNavOpen(false);
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  async function submit(event?: FormEvent, suggestedQuery?: string) {
    event?.preventDefault();
    const trimmed = (suggestedQuery ?? query).trim();
    if (!trimmed || loading) return;

    setQuery(suggestedQuery ?? query);
    setLoading(true);
    setStage("analyzing");
    setStreamText("");

    try {
      const response = await fetch(`${API_BASE}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed, persona, chat_history: chatHistory })
      });

      if (!response.ok || !response.body) throw new Error(`Backend returned HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalResult: RagResult | undefined;
      let streamedAnswer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const eventText of events) {
          const line = eventText.split("\n").find((entry) => entry.startsWith("data: "));
          if (!line) continue;
          const payload = JSON.parse(line.slice(6));
          if (payload.error) throw new Error(payload.error);
          if (payload.stage) setStage(payload.stage);
          if (payload.stage === "token" && typeof payload.token === "string") {
            streamedAnswer += payload.token;
            setStreamText(streamedAnswer);
          }
          if (payload.stage === "complete") finalResult = payload.result as RagResult;
        }
      }

      const id = `${Date.now()}`;
      setMessages((current) => [
        ...current,
        { id, query: trimmed, answer: finalResult?.answer ?? streamedAnswer, result: finalResult }
      ]);
      setActiveId(id);
      setQuery("");
      setStreamText("");
      setStage("");
    } catch (error) {
      const id = `${Date.now()}`;
      const answer = error instanceof Error ? error.message : "Unable to reach backend";
      setMessages((current) => [...current, { id, query: trimmed, answer, failed: true }]);
      setActiveId(id);
      setStreamText("");
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  function startNewConversation() {
    if (loading) return;
    setMessages([]);
    setActiveId(null);
    setQuery("");
    setMobileNavOpen(false);
  }

  return (
    <main className="app-shell">
      <AnimatePresence>
        {mobileNavOpen ? (
          <motion.button
            aria-label="Close navigation"
            className="nav-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setMobileNavOpen(false)}
            type="button"
          />
        ) : null}
      </AnimatePresence>

      <aside className={`sidebar ${mobileNavOpen ? "open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">N</div>
          <div className="brand-copy">
            <strong>NETSOL Intelligence</strong>
            <span>Knowledge workspace</span>
          </div>
          <button className="icon-button mobile-only" onClick={() => setMobileNavOpen(false)} title="Close navigation" type="button">
            <PanelLeftClose size={18} />
          </button>
        </div>

        <button className="new-chat-button" onClick={startNewConversation} type="button">
          <Plus size={17} />
          New conversation
        </button>

        <nav className="history-section" aria-label="Recent conversations">
          <div className="section-heading">
            <span><History size={14} /> Recent</span>
            <span>{messages.length}</span>
          </div>
          <div className="history-list">
            {messages.length ? (
              messages.map((message) => (
                <button
                  className={`history-item ${activeId === message.id ? "active" : ""}`}
                  key={message.id}
                  onClick={() => {
                    setActiveId(message.id);
                    setMobileNavOpen(false);
                  }}
                  type="button"
                >
                  <MessageSquare size={15} />
                  <span>{message.query}</span>
                </button>
              ))
            ) : (
              <p className="history-empty">Your recent questions will appear here.</p>
            )}
          </div>
        </nav>

        <div className="system-panel">
          <div className="system-title">
            <span className={`status-dot ${health?.status === "ok" ? "online" : ""}`} />
            <div>
              <strong>{health?.status === "ok" ? "Systems operational" : healthError ? "Backend offline" : "Connecting"}</strong>
              <span>{health?.model ?? "Checking services"}</span>
            </div>
            <button className="icon-button" onClick={loadHealth} title="Refresh status" type="button">
              <RefreshCw size={15} />
            </button>
          </div>
          <div className="corpus-stats">
            <span><strong>{formatNumber(health?.web_chunks)}</strong> web</span>
            <span><strong>{formatNumber(health?.pdf_chunks)}</strong> PDF</span>
            <span><strong>{formatNumber(health?.metadata_records)}</strong> records</span>
          </div>
        </div>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button mobile-menu" onClick={() => setMobileNavOpen(true)} title="Open navigation" type="button">
              <Menu size={20} />
            </button>
            <div>
              <strong>Ask NETSOL</strong>
              <span>Answers grounded in your enterprise corpus</span>
            </div>
          </div>
          <div className="persona-control" aria-label="Response persona">
            {personas.map((item) => (
              <button
                className={persona === item.value ? "active" : ""}
                key={item.value}
                onClick={() => setPersona(item.value)}
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>
        </header>

        <div className="workspace">
          <motion.section className="conversation" aria-live="polite" layout transition={springTransition}>
            {!activeMessage && !loading ? (
              <WelcomeState onSelect={(value) => submit(undefined, value)} />
            ) : null}

            <AnimatePresence mode="wait">
              {loading ? (
                <ResponseView
                  answer={streamText}
                  key="streaming"
                  loading
                  query={query}
                  stage={stage}
                />
              ) : activeMessage ? (
                <ResponseView
                  answer={activeMessage.answer}
                  failed={activeMessage.failed}
                  key={activeMessage.id}
                  query={activeMessage.query}
                  result={activeMessage.result}
                />
              ) : null}
            </AnimatePresence>
            <div ref={answerEndRef} />
          </motion.section>

          <form className="composer-wrap" onSubmit={(event) => submit(event)}>
            <motion.div className="composer" layout transition={springTransition}>
              <textarea
                aria-label="Ask NETSOL"
                disabled={loading}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask about products, financial reports, clients, or technical documentation..."
                ref={textareaRef}
                rows={1}
                value={query}
              />
              {query && !loading ? (
                <button className="clear-button" onClick={() => setQuery("")} title="Clear question" type="button">
                  <X size={16} />
                </button>
              ) : null}
              <button className="send-button" disabled={loading || !query.trim()} title="Send question" type="submit">
                {loading ? <span className="button-loader" /> : <ArrowUp size={19} />}
              </button>
            </motion.div>
            <p className="composer-note">NETSOL Intelligence can make mistakes. Verify important details against the cited sources.</p>
          </form>
        </div>
      </section>
    </main>
  );
}

function WelcomeState({ onSelect }: { onSelect: (query: string) => void }) {
  return (
    <motion.div className="welcome-state" initial={{ y: 14 }} animate={{ y: 0 }} transition={springTransition}>
      <div className="welcome-icon"><Sparkles size={22} /></div>
      <p className="eyebrow">NETSOL enterprise knowledge</p>
      <h1>What would you like to know?</h1>
      <p className="welcome-copy">Search product documentation, company reports, and verified web content from one focused workspace.</p>
      <div className="suggestion-grid">
        {suggestions.map((suggestion, index) => {
          const Icon = suggestion.icon;
          return (
            <motion.button
              className="suggestion-card"
              initial={{ y: 10 }}
              animate={{ y: 0 }}
              whileHover={{ y: -3 }}
              whileTap={{ scale: 0.985 }}
              transition={{ ...springTransition, delay: 0.06 * index }}
              key={suggestion.label}
              onClick={() => onSelect(suggestion.query)}
              type="button"
            >
              <Icon size={18} />
              <span><strong>{suggestion.label}</strong>{suggestion.query}</span>
              <ArrowUp size={16} />
            </motion.button>
          );
        })}
      </div>
    </motion.div>
  );
}

function ResponseView({
  query,
  answer,
  result,
  loading,
  stage,
  failed
}: {
  query: string;
  answer: string;
  result?: RagResult;
  loading?: boolean;
  stage?: string;
  failed?: boolean;
}) {
  const verification = verificationCopy(result);
  const sources = result?.sources ?? [];

  return (
    <motion.div className="response-flow" layout initial={{ y: 14 }} animate={{ y: 0 }} exit={{ opacity: 0, y: -8 }} transition={springTransition}>
      <div className="user-question">
        <span>You</span>
        <p>{query}</p>
      </div>

      <motion.article className="assistant-response" layout transition={springTransition}>
        <header className="response-header">
          <div className="assistant-identity">
            <div className="assistant-mark"><Sparkles size={17} /></div>
            <div><strong>NETSOL Intelligence</strong><span>Source-grounded response</span></div>
          </div>
          {!loading && !failed ? (
            <span className={`verification-badge ${verification.tone}`}><ShieldCheck size={14} />{verification.label}</span>
          ) : failed ? (
            <span className="verification-badge danger">Response unavailable</span>
          ) : null}
        </header>

        {loading && !answer ? <ThinkingState stage={stage} /> : (
          <div className={`answer-content ${failed ? "error-answer" : ""}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer}</ReactMarkdown>
            {loading ? <span className="stream-cursor" /> : null}
          </div>
        )}

        {result ? (
          <footer className="response-footer">
            <div className="confidence-summary">
              <span className="confidence-dot" style={{ "--confidence": `${Math.round(result.confidence * 100)}%` } as React.CSSProperties} />
              <strong>{Math.round(result.confidence * 100)}% confidence</strong>
            </div>
            <span>{result.chunks_used} evidence passages</span>
            <span>{result.processing_time.toFixed(1)}s</span>
            <details className="technical-details">
              <summary>Details <ChevronDown size={14} /></summary>
              <div><span>Intent</span><strong>{result.intent}</strong><span>Route</span><strong>{result.route}</strong><span>Answer type</span><strong>{result.answer_type}</strong></div>
            </details>
          </footer>
        ) : null}
      </motion.article>

      {sources.length ? <SourceSection sources={sources} /> : null}
    </motion.div>
  );
}

function ThinkingState({ stage }: { stage?: string }) {
  const activeIndex = stage === "analyzing" ? 0 : stage === "streaming" ? 1 : 2;
  return (
    <div className="thinking-state">
      {["Understand", "Retrieve", "Compose"].map((label, index) => (
        <div className={index <= activeIndex ? "active" : ""} key={label}>
          <span>{index < activeIndex ? <Check size={13} /> : index + 1}</span>
          <p><strong>{label}</strong>{index === activeIndex ? stageLabels[stage ?? "analyzing"] : ""}</p>
        </div>
      ))}
    </div>
  );
}

function SourceSection({ sources }: { sources: string[] }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <section className="sources-section">
      <button className="sources-heading" onClick={() => setExpanded((value) => !value)} type="button">
        <span><BookOpen size={17} /><strong>Sources</strong><em>{sources.length}</em></span>
        <ChevronDown className={expanded ? "rotated" : ""} size={18} />
      </button>
      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div className="source-grid" layout initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={springTransition}>
            {sources.map((source, index) => {
              const details = sourceDetails(source);
              const Icon = details.isPdf ? FileText : Globe2;
              const content = (
                <>
                  <span className="source-index">{index + 1}</span>
                  <Icon size={17} />
                  <span className="source-copy"><strong>{details.title}</strong><small>{details.detail}</small></span>
                  {details.href ? <ExternalLink size={15} /> : null}
                </>
              );
              return details.href ? (
                <motion.a className="source-card" href={details.href} key={`${source}-${index}`} layout rel="noreferrer" target="_blank" whileHover={{ y: -2 }} transition={springTransition}>{content}</motion.a>
              ) : (
                <motion.div className="source-card" key={`${source}-${index}`} layout whileHover={{ y: -2 }} transition={springTransition}>{content}</motion.div>
              );
            })}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}
