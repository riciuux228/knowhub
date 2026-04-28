import { useState, useEffect, useRef, useCallback, memo } from 'react';
import { ExternalLink } from 'lucide-react';
import { sanitize } from '../sanitize';

interface TocItem {
  id: string;
  text: string;
  level: number;
}

interface READMEViewProps {
  htmlContent: string;
  githubUrl?: string;
  loading?: boolean;
  onAskAI?: () => void;
}

// Memoized README content — only re-renders when htmlContent changes
const ReadmeContent = memo(({ htmlContent }: { htmlContent: string }) => (
  <div className="github-readme readme-with-code-copy" dangerouslySetInnerHTML={{ __html: sanitize(htmlContent) }}
    style={{ fontSize: '0.9rem', lineHeight: 1.7, color: 'var(--text)', wordBreak: 'break-word', overflowWrap: 'break-word' }} />
));

export default function READMEView({ htmlContent, githubUrl, loading, onAskAI }: READMEViewProps) {
  const [toc, setToc] = useState<TocItem[]>([]);
  const [altReadme, setAltReadme] = useState<{ path: string; html: string } | null>(null);
  const [altLoading, setAltLoading] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const progressBarRef = useRef<HTMLDivElement>(null);
  const tocBtnRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const headingRefs = useRef<Map<string, HTMLElement>>(new Map());
  const activeIdRef = useRef('');

  // Event delegation handler for .md links — single handler on container
  const handleContentClick = useCallback((e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest('a');
    if (!a || !githubUrl) return;
    const href = a.getAttribute('href') || '';
    if (!href) return;

    // Only intercept .md links that are relative (not already rewritten to http)
    if (/\.md$/i.test(href) || /\/readme/i.test(href)) {
      if (href.startsWith('http')) return; // already rewritten, skip
      e.preventDefault();
      e.stopPropagation();
      const mdPath = href.replace(/^\//, '');
      setAltLoading(true);
      const [owner, repo] = (githubUrl.replace('https://github.com/', '')).split('/');
      fetch(`/api/github/discover/readme?full_name=${encodeURIComponent(owner + '/' + repo)}&path=${encodeURIComponent(mdPath)}`)
        .then(r => r.json())
        .then(d => {
          if (d.readme) {
            setAltReadme({ path: mdPath, html: d.readme });
          } else {
            const blobBase = githubUrl.replace(/\/$/, '') + '/blob/HEAD';
            window.open(blobBase + (href.startsWith('/') ? href : '/' + href), '_blank');
          }
        })
        .catch(() => {
          const blobBase = githubUrl.replace(/\/$/, '') + '/blob/HEAD';
          window.open(blobBase + (href.startsWith('/') ? href : '/' + href), '_blank');
        })
        .finally(() => setAltLoading(false));
    }
  }, [githubUrl]);

  // Extract TOC from HTML headings and add IDs — runs once per htmlContent
  useEffect(() => {
    if (!htmlContent || !contentRef.current) return;

    const container = contentRef.current;
    const headings = container.querySelectorAll('h1, h2, h3, h4, h5, h6');
    const items: TocItem[] = [];
    const idCount: Record<string, number> = {};

    headings.forEach((el) => {
      const text = el.textContent?.trim() || '';
      if (!text) return;

      let id = text.toLowerCase().replace(/[^\w一-鿿]+/g, '-').replace(/^-|-$/g, '');
      if (idCount[id] !== undefined) {
        idCount[id]++;
        id += `-${idCount[id]}`;
      } else {
        idCount[id] = 0;
      }

      el.id = id;
      headingRefs.current.set(id, el as HTMLElement);

      const level = parseInt(el.tagName.charAt(1));
      items.push({ id, text, level });
    });

    setToc(items);

    // Add copy buttons to code blocks
    const codeBlocks = container.querySelectorAll('pre');
    codeBlocks.forEach((pre) => {
      if (pre.querySelector('.code-copy-btn')) return;

      const wrapper = document.createElement('div');
      wrapper.style.position = 'relative';
      wrapper.className = 'code-block-wrapper';
      pre.parentNode?.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);

      const btn = document.createElement('button');
      btn.className = 'code-copy-btn';
      btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
      btn.title = '复制代码';
      btn.style.cssText = 'position:absolute;top:8px;right:8px;background:var(--surface3);border:1px solid var(--border);border-radius:6px;padding:6px;cursor:pointer;color:var(--text2);opacity:0;transition:opacity 0.2s;display:flex;align-items:center;z-index:2;';

      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const code = pre.querySelector('code');
        const text = code?.textContent || pre.textContent || '';
        try {
          await navigator.clipboard.writeText(text);
          btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
          btn.style.color = '#10b981';
          setTimeout(() => {
            btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
            btn.style.color = 'var(--text2)';
          }, 2000);
        } catch {}
      });

      wrapper.appendChild(btn);
      wrapper.addEventListener('mouseenter', () => { btn.style.opacity = '1'; });
      wrapper.addEventListener('mouseleave', () => { btn.style.opacity = '0'; });
    });

    // Rewrite relative links/images to absolute GitHub URLs
    if (githubUrl) {
      const repoBase = githubUrl.replace(/\/$/, '');
      const rawBase = repoBase.replace('https://github.com/', 'https://raw.githubusercontent.com/') + '/HEAD';
      const blobBase = repoBase + '/blob/HEAD';

      container.querySelectorAll('a[href]').forEach(el => {
        const a = el as HTMLAnchorElement;
        const href = a.getAttribute('href') || '';
        if (!href || href.startsWith('http') || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('javascript:')) return;
        const resolved = href.startsWith('/') ? href : '/' + href;

        // .md links → keep as relative so event delegation can intercept
        if (/\.md$/i.test(resolved) || /\/readme/i.test(resolved)) {
          a.style.cursor = 'pointer';
          a.removeAttribute('target');
          // Store the resolved path as href for delegation to pick up
          a.setAttribute('href', resolved);
          return;
        }

        // Other relative links → open externally
        a.setAttribute('href', blobBase + resolved);
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener');
      });

      container.querySelectorAll('img[src]').forEach(img => {
        const src = img.getAttribute('src') || '';
        if (!src || src.startsWith('http') || src.startsWith('data:')) return;
        const resolved = src.startsWith('/') ? src : '/' + src;
        img.setAttribute('src', rawBase + resolved);
      });
    }
  }, [htmlContent, githubUrl]);

  // Reset alt readme when main content changes
  useEffect(() => { setAltReadme(null); }, [htmlContent]);

  // Scroll handler — pure DOM manipulation, zero React re-renders
  const handleScroll = useCallback(() => {
    const container = contentRef.current;
    if (!container) return;

    const scrollTop = container.scrollTop;
    const scrollHeight = container.scrollHeight - container.clientHeight;
    const pct = scrollHeight > 0 ? (scrollTop / scrollHeight) * 100 : 0;

    // Update progress bar directly
    if (progressBarRef.current) {
      progressBarRef.current.style.width = `${pct}%`;
    }

    // Find active heading
    let newActiveId = '';
    for (const item of toc) {
      const el = headingRefs.current.get(item.id);
      if (el) {
        const top = el.offsetTop - container.offsetTop;
        if (scrollTop >= top - 80) {
          newActiveId = item.id;
        }
      }
    }

    // Update TOC button styles directly (only if changed)
    if (newActiveId && newActiveId !== activeIdRef.current) {
      const oldBtn = tocBtnRefs.current.get(activeIdRef.current);
      if (oldBtn) {
        oldBtn.style.background = 'transparent';
        oldBtn.style.color = 'var(--text2)';
        oldBtn.style.borderLeftColor = 'transparent';
      }
      const newBtn = tocBtnRefs.current.get(newActiveId);
      if (newBtn) {
        newBtn.style.background = 'var(--accent-dim)';
        newBtn.style.color = 'var(--accent)';
        newBtn.style.borderLeftColor = 'var(--accent)';
      }
      activeIdRef.current = newActiveId;
    }
  }, [toc]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    container.addEventListener('scroll', handleScroll, { passive: true });
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  const scrollToHeading = (id: string) => {
    const el = headingRefs.current.get(id);
    if (el && contentRef.current) {
      const top = el.offsetTop - contentRef.current.offsetTop;
      contentRef.current.scrollTo({ top: top - 20, behavior: 'smooth' });
    }
  };

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text2)' }}>
      <div className="loading-dots" style={{ fontSize: '1rem' }}>加载中</div>
    </div>;
  }

  if (!htmlContent) {
    return <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text2)' }}>
      <p>暂无 README 内容</p>
    </div>;
  }

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, position: 'relative' }}>
      {/* Progress bar — updated via ref, no re-render */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: '3px', zIndex: 10, background: 'var(--surface3)' }}>
        <div ref={progressBarRef} style={{ height: '100%', width: '0%', background: 'linear-gradient(90deg, var(--accent), var(--cyan, #06b6d4))', borderRadius: '0 2px 2px 0' }} />
      </div>

      {/* TOC Sidebar — buttons updated via refs, no re-render */}
      {toc.length > 1 && !altReadme && (
        <div style={{
          width: '220px', minWidth: '220px', borderRight: '1px solid var(--border)',
          padding: '16px 0', overflowY: 'auto', flexShrink: 0, marginTop: '3px',
          position: 'sticky', top: 0, alignSelf: 'flex-start', maxHeight: '100%',
        }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', padding: '0 16px 8px', letterSpacing: '1px', textTransform: 'uppercase' }}>
            目录
          </div>
          {toc.map(item => (
            <button key={item.id} ref={el => { if (el) tocBtnRefs.current.set(item.id, el); }}
              onClick={() => scrollToHeading(item.id)} style={{
              display: 'block', width: '100%', textAlign: 'left', border: 'none',
              background: 'transparent',
              color: 'var(--text2)',
              padding: '6px 16px', paddingLeft: `${16 + (item.level - 1) * 12}px`,
              fontSize: item.level <= 2 ? '0.82rem' : '0.78rem',
              fontWeight: item.level === 1 ? 600 : 400,
              cursor: 'pointer',
              borderLeft: '2px solid transparent',
              lineHeight: 1.4, wordBreak: 'break-all',
            }}>
              {item.text}
            </button>
          ))}
        </div>
      )}

      {/* Main content — memoized, never re-renders from scroll */}
      <div ref={contentRef} onClick={handleContentClick} style={{
        flex: 1, overflow: 'auto', padding: '24px', paddingTop: '27px',
      }}>
        {/* Alt README banner */}
        {altReadme && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px', padding: '10px 14px', borderRadius: '8px', background: 'var(--accent-dim)', border: '1px solid var(--accent)' }}>
            <span style={{ fontSize: '0.85rem', color: 'var(--accent)', fontWeight: 600 }}>📄 {altReadme.path}</span>
            <button onClick={() => setAltReadme(null)} style={{ marginLeft: 'auto', padding: '4px 12px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.8rem' }}>← 返回主 README</button>
          </div>
        )}
        {altLoading && (
          <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text2)' }}>
            <span className="loading-dots">加载中</span>
          </div>
        )}

        <div style={{ display: 'flex', gap: '8px', marginBottom: '20px', flexWrap: 'wrap' }}>
          {githubUrl && (
            <a href={githubUrl} target="_blank" rel="noopener" style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              padding: '8px 14px', borderRadius: '8px',
              background: 'var(--surface2)', border: '1px solid var(--border)',
              color: 'var(--text2)', textDecoration: 'none', fontSize: '0.85rem',
              transition: 'all 0.2s',
            }} onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text2)'; }}>
              <ExternalLink size={14} />
              在 GitHub 上查看
            </a>
          )}
          {onAskAI && (
            <button onClick={onAskAI} style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              padding: '8px 14px', borderRadius: '8px',
              background: 'var(--accent)', border: 'none',
              color: '#fff', fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer',
            }}>
              🤖 AI 问答
            </button>
          )}
        </div>
        {altReadme ? (
          <div className="github-readme readme-with-code-copy" dangerouslySetInnerHTML={{ __html: sanitize(altReadme.html) }}
            style={{ fontSize: '0.9rem', lineHeight: 1.7, color: 'var(--text)', wordBreak: 'break-word', overflowWrap: 'break-word' }} />
        ) : (
          <ReadmeContent htmlContent={htmlContent} />
        )}
      </div>
    </div>
  );
}
