import { useState, useRef, useEffect } from 'react';
import { Send, Trash2, Zap } from 'lucide-react';
import { marked } from 'marked';
import { sanitize } from '../sanitize';
import hljs from 'highlight.js';
import markedKatex from 'marked-katex-extension';

// Configure marked with KaTeX (same as App.tsx)
try { marked.use(markedKatex({ throwOnError: false, nonStandard: true })); } catch {}

type LoadPhase = 'idle' | 'connecting' | 'searching' | 'generating';

const phaseInfo: Record<LoadPhase, { icon: string; text: string; color: string }> = {
  idle:        { icon: '', text: '', color: '' },
  connecting:  { icon: '🔗', text: '建立神经连接', color: 'var(--cyan, #06b6d4)' },
  searching:   { icon: '🔍', text: '知识库检索中', color: 'var(--accent2, #f59e0b)' },
  generating:  { icon: '🧠', text: '深度推理生成', color: 'var(--accent)' },
};

export default function AIChat({ onOpenItem }: { onOpenItem: (id: string) => void }) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [messages, setMessages] = useState<{role: string, content: string, sources?: any[]}[]>(() => {
    try {
      const saved = localStorage.getItem('knowhub_chat_memory');
      if (saved) return JSON.parse(saved);
    } catch {}
    return [{ role: 'ai', content: '嗨！我是小可，你的知识小助手～有什么想聊的随时扔给我哦！' }];
  });

  // Persist messages on a debounce to avoid write amplification during streaming
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => {
      localStorage.setItem('knowhub_chat_memory', JSON.stringify(messages));
    }, 1000);
    return () => { if (persistTimer.current) clearTimeout(persistTimer.current); };
  }, [messages]);

  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [phase, setPhase] = useState<LoadPhase>('idle');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
    // Syntax highlighting
    document.querySelectorAll('.chat-bubble pre code').forEach((el) => {
      hljs.highlightElement(el as HTMLElement);
    });
    // Add copy buttons to code blocks (same as GitHub README)
    document.querySelectorAll('.chat-bubble pre').forEach((pre) => {
      if (pre.parentElement?.classList.contains('code-block-wrapper')) return;
      const wrapper = document.createElement('div');
      wrapper.className = 'code-block-wrapper';
      pre.parentNode?.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);
      const btn = document.createElement('button');
      btn.className = 'code-copy-btn';
      btn.textContent = '复制';
      btn.style.cssText = 'position:absolute;top:8px;right:8px;opacity:0;background:var(--surface);color:var(--text-dim);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:0.7rem;cursor:pointer;transition:opacity 0.2s;';
      btn.onclick = () => {
        const code = pre.querySelector('code');
        navigator.clipboard.writeText(code?.textContent || '');
        btn.textContent = '已复制';
        setTimeout(() => { btn.textContent = '复制'; }, 1500);
      };
      wrapper.style.position = 'relative';
      wrapper.appendChild(btn);
    });
  }, [messages]);

  // Phase timer: connecting → searching → generating
  useEffect(() => {
    if (!loading) { setPhase('idle'); return; }
    setPhase('connecting');
    const t1 = setTimeout(() => setPhase('searching'), 2000);
    const t2 = setTimeout(() => setPhase('generating'), 5000);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [loading]);

  const sendChat = async () => {
    if (!input.trim() || loading) return;
    const question = input.trim();
    setInput('');
    setLoading(true);

    const newMsgs = [...messages, { role: 'user', content: question }];
    setMessages([...newMsgs, { role: 'ai', content: '' }]);

    try {
      const historyForBackend = messages
        .filter(m => m.role === 'user' || m.role === 'ai')
        .map(m => ({ role: m.role === 'ai' ? 'assistant' : 'user', content: m.content }));

      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ question, history: historyForBackend })
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => res.statusText);
        throw new Error(`API error ${res.status}: ${errText}`);
      }

      setPhase('generating');
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response stream');
      const decoder = new TextDecoder();
      let answer = '';
      let currentSources: any[] = [];

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;

        const textArea = decoder.decode(value, { stream: true });
        const lines = textArea.split('\n');

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;

          try {
            const parsed = JSON.parse(data);
            if (parsed.content) answer += parsed.content;
            if (parsed.sources) currentSources = parsed.sources;

            setMessages(prev => {
              const clone = [...prev];
              clone[clone.length - 1] = {
                role: 'ai',
                content: answer,
                sources: currentSources.length > 0 ? currentSources : undefined
              };
              return clone;
            });
          } catch {}
        }
      }
    } catch (e: any) {
      console.error('[AIChat] Error:', e?.message || e);
      setMessages(prev => {
        const clone = [...prev];
        clone[clone.length - 1] = { role: 'ai', content: `AI 服务连接失败: ${e?.message || '未知错误'}` };
        return clone;
      });
    }
    setLoading(false);
  };

  const clearChat = () => {
    setMessages([{ role: 'ai', content: '记忆已重置。你好！我是您的局域网智能中枢。随时丢给我任何问题或文件。' }]);
    setShowConfirm(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', borderRadius: '12px', background: 'var(--surface)' }}>
      {showConfirm && (
        <div className="modal-overlay active" style={{ zIndex: 10000, position: 'absolute' }}>
           <div className="item-card" style={{ width: '300px', display: 'flex', flexDirection: 'column', gap: '15px', padding: '20px', zIndex: 10001 }}>
              <h3 style={{ margin: 0, color: 'var(--text)', fontSize: '1.05rem', display: 'flex', alignItems: 'center', gap: '6px' }}><Zap size={18} color="var(--accent)" />清除记录？</h3>
              <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>确定要格式化并切断当前的 AI 神经记忆链路吗？</div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '10px' }}>
                 <button className="btn" onClick={() => setShowConfirm(false)} style={{ padding: '6px 12px', fontSize: '0.8rem' }}>取消</button>
                 <button className="btn btn-primary" onClick={clearChat} style={{ padding: '6px 12px', fontSize: '0.8rem', background: 'var(--red)', borderColor: 'var(--red)' }}>物理格式化</button>
              </div>
           </div>
        </div>
      )}
      <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.02)' }}>
        <span style={{ fontSize: '1.05rem', fontWeight: 'bold', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '8px' }}><Zap size={18} color="var(--accent)" />系统神经网络</span>
        <button className="icon-btn" onClick={() => setShowConfirm(true)} title="清除记忆节点"><Trash2 size={16} /></button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {messages.map((msg, idx) => (
          <div key={idx} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', width: '100%' }}>
            <div style={{
               maxWidth: '85%',
               padding: '14px 18px',
               lineHeight: '1.6',
               fontSize: '0.95rem',
               borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
               background: msg.role === 'user' ? 'linear-gradient(135deg, var(--accent), #5a4bcf)' : 'rgba(255,255,255,0.03)',
               color: msg.role === 'user' ? '#fff' : 'var(--text)',
               border: msg.role === 'user' ? 'none' : '1px solid var(--border)',
               boxShadow: msg.role === 'user' ? '0 4px 15px rgba(139, 92, 246, 0.3)' : '0 2px 10px rgba(0,0,0,0.1)',
               backdropFilter: msg.role === 'user' ? 'none' : 'blur(10px)',
            }}>
              {msg.role === 'ai' && !msg.content && loading ? (
                 <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                   <div className="phase-spinner" style={{
                     width: '18px', height: '18px',
                     border: `2px solid ${phaseInfo[phase].color}33`,
                     borderTopColor: phaseInfo[phase].color,
                     borderRadius: '50%',
                     animation: 'spin 0.8s linear infinite',
                   }} />
                   <span style={{ color: phaseInfo[phase].color, fontSize: '0.9rem', fontWeight: 500 }}>
                     {phaseInfo[phase].icon} {phaseInfo[phase].text}
                     <span className="loading-dots" />
                   </span>
                 </div>
              ) : (
                 <div className="markdown-body readme-with-code-copy" dangerouslySetInnerHTML={{ __html: sanitize(marked.parse(msg.content) as string) }} />
              )}
              {msg.sources && msg.sources.length > 0 && (
                <div className="chat-sources" style={{ marginTop: '14px', paddingTop: '12px', borderTop: msg.role === 'user' ? '1px solid rgba(255,255,255,0.2)' : '1px solid var(--border)', fontSize: '0.8rem' }}>
                  <div style={{ paddingBottom: '8px', color: msg.role === 'user' ? 'rgba(255,255,255,0.8)' : 'var(--text-dim)' }}>参考引流脉络：</div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    {msg.sources.map((s, i) => (
                       <a key={i} href="#" onClick={(e) => { e.preventDefault(); onOpenItem(s.id); }} style={{
                          padding: '4px 10px',
                          borderRadius: '6px',
                          background: msg.role === 'user' ? 'rgba(255,255,255,0.2)' : 'var(--surface2)',
                          border: msg.role === 'user' ? 'none' : '1px solid var(--border)',
                          textDecoration: 'none',
                          color: msg.role === 'user' ? '#fff' : 'var(--accent)',
                          fontSize: '0.75rem',
                          transition: 'all 0.2s',
                          display: 'inline-block'
                       }} onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-2px)'} onMouseLeave={e => e.currentTarget.style.transform = 'none'}>
                         [{i + 1}] {s.title || s.type}
                       </a>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div style={{ padding: '20px 24px', background: 'rgba(255,255,255,0.02)', borderTop: '1px solid var(--border)', borderBottomLeftRadius: '12px', borderBottomRightRadius: '12px' }}>
        <div className="chat-input-wrap" style={{ borderRadius: '24px', padding: '6px 12px 6px 18px', background: 'var(--surface2)', boxShadow: 'inset 0 2px 4px rgba(0,0,0,0.2), 0 0 0 1px var(--border) transparent', alignItems: 'center' }}>
           <textarea
             value={input}
             onChange={e => setInput(e.target.value)}
             onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
             placeholder="问我关于您的文件、代码和灵感的任何事..."
             className="chat-input"
             style={{ margin: 0, padding: '10px 0', minHeight: '44px', display: 'flex', alignItems: 'center' }}
           />
           <button onClick={sendChat} disabled={loading || !input.trim()} className="chat-send" style={{ borderRadius: '50%', width: '40px', height: '40px', padding: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 10px rgba(139, 92, 246, 0.3)' }}>
             <Send size={18} style={{ marginLeft: '-2px' }} />
           </button>
        </div>
      </div>
    </div>
  );
}
