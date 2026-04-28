import { useState, useEffect, useRef } from 'react';
import { Search, FileText, MessageSquare, Trash2, Download, Copy, Upload, ArrowLeft, Sun, Moon, Database, FolderDown, Settings, Lock, Zap, Image as ImageIcon, FolderPlus, Folder, Star, Bell, TrendingUp, Tag, RefreshCw, ExternalLink, X, GitBranch, Wrench } from 'lucide-react';
import { marked } from 'marked';
import { sanitize } from './sanitize';
import markedKatex from 'marked-katex-extension';
import hljs from 'highlight.js';
import JSZip from 'jszip';
import ForceGraph2D from 'react-force-graph-2d';
import AIChat from './components/AIChat';
import READMEView from './components/READMEView';
import type { Item, GitHubRepo, CrossRefs, Collection, Space, DigestConfig, GitHubCategory, TagStat, GraphData, GitHubAccount, GitHubRelease, SuggestedCollection, ReleaseAsset, Subscription, TrendingItem } from './types';
import './index.css';

marked.use(markedKatex({ throwOnError: false, nonStandard: true }));

function App() {
  const [items, setItems] = useState<Item[]>([]);
  const [stats, setStats] = useState({ total: 0, files: 0, texts: 0, codes: 0, total_size: 0 });
  const [view, setView] = useState('kb');
  const [searchQuery, setSearchQuery] = useState('');
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState<{ msg: string, type: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isLocked, setIsLocked] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<{ isOpen: boolean, msg: string, onConfirm: () => void } | null>(null);

  const showToast = (msg: string, type: string = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  };
  const [detailItem, setDetailItem] = useState<Item | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [relatedItems, setRelatedItems] = useState<Item[]>([]);
  const [crossRefs, setCrossRefs] = useState<CrossRefs>({github_repos: [], related_items: []});
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark');
  const [toolsTab, setToolsTab] = useState('ai');
  const [kbType, setKbType] = useState('');
  const [uploadLogs, setUploadLogs] = useState<string[]>([]);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  // Hook up server sent events for real-time logs + auto-refresh
  useEffect(() => {
    let evtSource: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      evtSource = new EventSource('/api/events');
      evtSource.onmessage = (e) => {
        setUploadLogs(prev => [...prev.slice(-100), e.data]);

        // Real-time synchronization: when backend reports successful save, trigger grid refresh
        if (e.data.includes('✅') || e.data.includes('🎉') || e.data.includes('沉淀') || e.data.includes('提取完成') || e.data.includes('写入成功')) {
          setRefreshTrigger(t => t + 1);
        }
      };
      evtSource.onerror = () => {
        evtSource?.close();
        // Reconnect after 3 seconds
        reconnectTimer = setTimeout(connect, 3000);
      };
    };
    connect();

    return () => {
      evtSource?.close();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  useEffect(() => {
    if (refreshTrigger > 0) {
      setPage(1);
      refreshData(1);
    }
  }, [refreshTrigger]);

  // DropZone / QuickAdd State
  const [quickInput, setQuickInput] = useState('');
  const [space, setSpace] = useState('all');
  const [autoRoute, setAutoRoute] = useState(false);
  const [spacesList, setSpacesList] = useState<Space[]>([]);
  const [deepSearch, setDeepSearch] = useState(false);

  // Collections state
  const [collectionsList, setCollectionsList] = useState<Collection[]>([]);
  const [activeCollection, setActiveCollection] = useState('');
  const [showCollectionModal, setShowCollectionModal] = useState(false);
  const [newCollName, setNewCollName] = useState('');
  const [newCollIcon, setNewCollIcon] = useState('📁');
  const [showAddToCollection, setShowAddToCollection] = useState<Item | null>(null); // item to add

  useEffect(() => {
    fetch('/api/spaces').then(r => r.json()).then(d => setSpacesList(d.spaces || [])).catch(() => {});
    fetch('/api/collections').then(r => r.json()).then(d => setCollectionsList(d.collections || [])).catch(() => {});
  }, []);

  const abortRef = useRef<AbortController | null>(null);

  const refreshData = async (newPage: number = 1) => {
    // Cancel any in-flight request to prevent race conditions
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let url = `/api/items?page=${newPage}&space=${space}`;
    if (activeCollection) {
      url = `/api/items?page=${newPage}&collection=${activeCollection}`;
    }
    if (searchQuery) {
      url += `&search=${encodeURIComponent(searchQuery)}`;
      if (deepSearch) url += '&rerank=true';
    }
    else if (view === 'kb' && kbType) url += `&type_filter=${kbType}`;
    else if (view !== 'all' && view !== 'ai' && view !== 'collection' && view !== 'kb') url += `&type_filter=${view}`;

    try {
      const res = await fetch(url, { signal: controller.signal });
      if (res.status === 401) {
        setIsLocked(true);
        return;
      }
      const data = await res.json();
      if (controller.signal.aborted) return;

      const newItems = data.items || [];

      if (newPage === 1) setItems(newItems);
      else setItems(prev => [...prev, ...newItems]);

      setHasMore(newItems.length === 30); // backend page_size is 30

      const st = await fetch('/api/stats', { signal: controller.signal });
      if (!controller.signal.aborted) setStats(await st.json());
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
    }
  };

  useEffect(() => {
    setPage(1);
    refreshData(1);
  }, [view, searchQuery, space, activeCollection, kbType]);

  // Handle Mobile Share Target API
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const text = params.get('share_text');
    const url = params.get('share_url');
    const title = params.get('share_title');

    if (text || url) {
      const content = `${text || ''}\n${url || ''}`.trim();
      const uploadShared = async () => {
        setIsUploading(true);
        const fd = new FormData();
        fd.append('content', content);
        fd.append('title', title || '');
        const res = await fetch('/api/text', { method: 'POST', body: fd });
        const data = await res.json();
        setIsUploading(false);
        const memTag = data.memory_stored ? ' · 🧠 记忆库已同步' : '';
        showToast(`从手机分享过来的内容已保存${memTag}`, 'success');
        refreshData();
        window.history.replaceState({}, '', '/');
      };
      uploadShared();
    }
  }, []);

  const loadMore = () => {
    const next = page + 1;
    setPage(next);
    refreshData(next);
  };

  // Global Paste Listener
  useEffect(() => {
    const handlePaste = async (e: ClipboardEvent) => {
      const items = e.clipboardData?.items || [];
      const hasImage = Array.from(items).some(i => i.type.startsWith('image/'));

      // Allow pasting images globally, even if inside an input!
      if (hasImage && e.clipboardData?.files.length) {
        e.preventDefault();
        await uploadFiles(e.clipboardData.files);
        return;
      }

      // Don't intercept text if user is typing in an input/textarea
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      const text = e.clipboardData?.getData('text');
      if (text) {
        const fd = new FormData();
        fd.append('content', text);
        fd.append('title', '');
        const res = await fetch('/api/text', { method: 'POST', body: fd });
        const data = await res.json();
        const memTag = data.memory_stored ? ' · 🧠 记忆库已同步' : '';
        showToast(`从剪切板自动保存成功!${memTag}`, 'success');
        setPage(1);
        refreshData(1);
      }
    };
    document.addEventListener('paste', handlePaste);
    return () => document.removeEventListener('paste', handlePaste);
  }, []);

  // Highlighting in details
  useEffect(() => {
    if (detailItem && !isEditing && detailItem.type !== 'file' && detailItem.type !== 'image') {
      document.querySelectorAll('.detail-content pre code').forEach(el => hljs.highlightElement(el as HTMLElement));
    }
  }, [detailItem, isEditing]);

  // Fetch Related items (Semantic Network) + Cross References
  useEffect(() => {
    if (detailItem) {
      setRelatedItems([]);
      setCrossRefs({github_repos: [], related_items: []});
      fetch(`/api/items/${detailItem.id}/related`)
        .then(res => res.json())
        .then(data => setRelatedItems(data.items || []))
        .catch(console.error);
      fetch(`/api/items/${detailItem.id}/crossrefs`)
        .then(res => res.json())
        .then((data: CrossRefs) => setCrossRefs(data))
        .catch(console.error);
    }
  }, [detailItem]);

  const deleteItem = async (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    setConfirmDialog({
      isOpen: true,
      msg: '确定彻底删除这条记录吗？删除后无法恢复。',
      onConfirm: async () => {
        try {
          await fetch(`/api/items/${id}`, { method: 'DELETE' });
          setPage(1);
          refreshData(1);
          if (detailItem?.id === id) setDetailItem(null);
          showToast('已删除', 'success');
        } catch { }
        setConfirmDialog(null);
      }
    });
  };

  const uploadFiles = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setUploadLogs([]);
    setIsUploading(true);
    let lastSuggestions: SuggestedCollection[] = [];
    let memSynced = false;
    for (let i = 0; i < files.length; i++) {
      const fd = new FormData();
      fd.append('file', files[i]);
      fd.append('space', autoRoute ? 'auto' : (space === 'all' ? 'default' : space));
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      const data = await res.json();
      lastSuggestions = data.suggested_collections || [];
      if (data.memory_stored) memSynced = true;
    }
    setTimeout(() => setIsUploading(false), 800);

    if (lastSuggestions.length > 0) {
      const names = lastSuggestions.map((s: SuggestedCollection) => `${s.icon} ${s.name}`).join('、');
      showToast(`💡 建议添加到收藏集：${names}${memSynced ? ' · 🧠 记忆库已同步' : ''}`, 'info');
    } else {
      showToast(`成功上传解析 ${files.length} 个文件${memSynced ? ' 🧠 记忆库已同步' : ''}`, 'success');
    }
    refreshData();
  };

  const uploadFolder = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setUploadLogs([]);
    setIsUploading(true);

    try {
      // Extract folder name from the first file's relative path
      const firstPath = (files[0] as File & { webkitRelativePath?: string }).webkitRelativePath || files[0].name;
      const folderName = firstPath.split('/')[0] || 'folder';

      setUploadLogs(prev => [...prev, `📁 正在打包文件夹 "${folderName}" (${files.length} 个文件)...`]);

      // Create ZIP archive from all files
      const zip = new JSZip();
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
        const data = await file.arrayBuffer();
        zip.file(relativePath, data);
      }

      setUploadLogs(prev => [...prev, `📦 压缩完成，正在上传 ZIP 包到服务端...`]);

      const zipBlob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } });
      const zipFile = new File([zipBlob], `${folderName}.zip`, { type: 'application/zip' });

      const fd = new FormData();
      fd.append('file', zipFile);
      fd.append('space', autoRoute ? 'auto' : (space === 'all' ? 'default' : space));
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      const data = await res.json();

      const memTag = data.memory_stored ? ' · 🧠 记忆库已同步' : '';
      showToast(`文件夹 "${folderName}" 已打包为 ZIP 并上传解析${memTag}`, 'success');
    } catch (e) {
      showToast('文件夹打包失败', 'error');
    }

    setTimeout(() => setIsUploading(false), 800);
    refreshData();
  };

  const submitQuickAdd = async () => {
    if (!quickInput.trim()) return;
    setIsUploading(true);
    setUploadLogs(['正在保存: ' + quickInput.slice(0, 20) + '...']);
    const fd = new FormData();
    fd.append('content', quickInput);
    fd.append('space', autoRoute ? 'auto' : (space === 'all' ? 'default' : space));

    try {
      const res = await fetch('/api/text', { method: 'POST', body: fd });
      const data = await res.json();
      setQuickInput('');
      setPage(1);
      refreshData(1);

      // Show collection suggestions if any
      const suggestions = data.suggested_collections || [];
      const memTag = data.memory_stored ? ' · 🧠 记忆库已同步' : '';
      if (suggestions.length > 0 && !data.duplicate) {
        const names = suggestions.map((s: SuggestedCollection) => `${s.icon} ${s.name}`).join('、');
        showToast(`💡 建议添加到收藏集：${names}${memTag}`, 'info');
      } else {
        showToast(`保存成功${memTag}`, 'success');
      }
    } catch { }
    setIsUploading(false);
  };

  const saveEdit = async () => {
    if (!detailItem || !editContent.trim()) return;
    const fd = new FormData();
    fd.append('content', editContent);
    fd.append('title', editTitle);
    await fetch(`/api/items/${detailItem.id}`, { method: 'PUT', body: fd });
    setIsEditing(false);
    showToast('内容已更新', 'success');

    // Simulate updating the active detail view and grid list optimistically
    setDetailItem({ ...detailItem, content: editContent, title: editTitle });
    setItems(items.map(i => i.id === detailItem.id ? { ...i, content: editContent, title: editTitle } : i));
  };

  const copyContent = async () => {
    if (detailItem && (detailItem.type === 'file' || detailItem.type === 'image') && detailItem.mime_type?.startsWith('image/')) {
      try {
        if (!navigator.clipboard || !navigator.clipboard.write) {
          showToast('处于局域网无加密(HTTP)环境，手机浏览器底层切断了媒体剪贴流模块。请直接「长按原图」在弹出的菜单里选择保存或复制哟！', 'info');
          return;
        }
        showToast('正在跨空间提取图片数据...', 'info');
        const res = await fetch(`/api/download/${detailItem.id}`);
        const originalBlob = await res.blob();

        const img = new Image();
        img.src = URL.createObjectURL(originalBlob);
        await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; });

        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        canvas.getContext('2d')?.drawImage(img, 0, 0);

        canvas.toBlob(async (pngBlob) => {
          if (!pngBlob) return showToast('图像像素解压异常', 'error');
          try {
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob })]);
            showToast('原画面流已强制转化为基准态 PNG 并封装入系统剪切板！', 'success');
          } catch (e) {
            showToast('您手机上的运行沙盒强硬地拒绝了应用层的剪贴板呼入。', 'error');
          }
        }, 'image/png');
      } catch (err) {
        showToast('核心内存池处理意外中断', 'error');
      }
    } else if (detailItem && detailItem.content) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(detailItem.content);
        showToast('文本流已无缝接入剪切板。', 'success');
      } else {
        showToast('处于非安全局域网，无法呼叫剪切板底层。请手动选中文字复制。', 'info');
      }
    }
  };

  const renderContent = () => {
    if (view === 'settings') {
      return <SettingsView onSave={() => { setView('all'); showToast('设置已保存并生效', 'success'); }} showToast={showToast} />
    }
    if (view === 'ai') return <AIChat onOpenItem={(id) => {
      const it = items.find(i => i.id === id);
      if (it) { setView('all'); setDetailItem(it); }
    }} />;

    if (view === 'graph') {
      return <KnowledgeGraphView theme={theme} onNodeClick={(node: { id: string; name: string; group: string; val: number }) => {
        const it = items.find(i => i.id === node.id);
        if (it) { setView('all'); setDetailItem(it); }
      }} />;
    }

    if (view === 'gallery') {
      return <GalleryView space={space} spacesList={spacesList} onSpaceChange={setSpace} onOpenItem={(item: Item) => setDetailItem(item)} />;
    }

    if (view === 'github') {
      return <GitHubStarsView showToast={showToast} />;
    }

    const isFile = detailItem ? (detailItem.type === 'file' || detailItem.type === 'image') : false;
    const showKBLayout = view === 'kb' || view === 'all' || view === 'file' || view === 'text' || view === 'code' || view === 'collection';

    const itemsView = (
      <div id="items-view" style={{ position: 'relative', flex: 1, minWidth: 0 }}>
        {/* Detail Item Modal Overlay */}
        {detailItem && (
          <div className="modal-overlay active" onClick={() => { setDetailItem(null); setIsEditing(false); }}>
            <div className="modal" onClick={e => e.stopPropagation()}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '16px' }}>
                <div onClick={() => { setDetailItem(null); setIsEditing(false); }} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', cursor: 'pointer', color: 'var(--text-dim)' }}>
                  <ArrowLeft size={16} /> 返回
                </div>
                <div style={{ display: 'flex', gap: '10px' }}>
                  {(!isFile || detailItem?.mime_type?.startsWith('image/')) && (
                    <button className="btn" onClick={copyContent} title="复制到剪切板">
                      <Copy size={16} />
                    </button>
                  )}
                  {!isFile && !isEditing && (
                    <button className="btn" onClick={() => {
                      setEditTitle(detailItem.title);
                      setEditContent(detailItem.content);
                      setIsEditing(true);
                    }}>编辑内容</button>
                  )}
                </div>
              </div>

              {isEditing ? (
                <div className="text-input-area" style={{ marginTop: '10px' }}>
                  <input className="title-input" value={editTitle} onChange={e => setEditTitle(e.target.value)} placeholder="标题（可选）" />
                  <textarea value={editContent} onChange={e => setEditContent(e.target.value)} style={{ minHeight: '300px' }}></textarea>
                  <div style={{ marginTop: '16px', textAlign: 'right' }}>
                    <button className="btn" onClick={() => setIsEditing(false)} style={{ marginRight: '10px' }}>取消</button>
                    <button className="btn btn-primary" onClick={saveEdit}>保存修改</button>
                  </div>
                </div>
              ) : (
                <div>
                  <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '12px' }}>
                    <span className={`item-type-badge badge-${detailItem.type}`}>{isFile ? (detailItem.type === 'image' ? '图片' : '文件') : detailItem.type === 'code' ? '代码' : '文字'}</span>
                    <h2 style={{ fontSize: '1.2rem' }}>{detailItem.title || '无标题'}</h2>
                  </div>
                  <p style={{ color: 'var(--text-dim)', marginBottom: '16px', fontSize: '0.85rem' }}>{detailItem.summary}</p>

                  <div style={{ display: 'flex', gap: '6px', marginBottom: '16px', flexWrap: 'wrap' }}>
                    {Array.isArray(detailItem.tags) && detailItem.tags.map((t: string) => (
                      <span key={t} className="tag" onClick={() => { setSearchQuery(t); setDetailItem(null); }}>{t}</span>
                    ))}
                  </div>

                  {isFile ? (
                    <div>
                      <a href={`/api/download/${detailItem.id}`} className="btn btn-primary" style={{ display: 'inline-flex', textDecoration: 'none', marginBottom: '12px' }}>下载文件</a>
                      {detailItem.mime_type?.startsWith('image/') && (
                        <div style={{ marginTop: '10px', marginBottom: '16px', textAlign: 'center', background: 'var(--bg)', padding: '10px', borderRadius: 'var(--radius)' }}>
                          <img
                            src={`/api/download/${detailItem.id}`}
                            alt={detailItem.title}
                            style={{ maxWidth: '100%', maxHeight: '60vh', objectFit: 'contain', borderRadius: '4px' }}
                          />
                        </div>
                      )}
                      {detailItem.content && (
                        <div className="detail-content markdown-body" style={{ marginTop: '10px' }}>
                          <div style={{ fontSize: '0.8rem', color: 'var(--accent)', marginBottom: '8px' }}>[MarkItDown 结构化提取内容]</div>
                          <div dangerouslySetInnerHTML={{ __html: sanitize(marked.parse(detailItem.content.substring(0, 8000)) as string) }} />
                        </div>
                      )}
                    </div>
                  ) : (
                    <div style={{ position: 'relative' }}>
                      <div className="detail-content markdown-body">
                        {detailItem.type === 'code' ? (
                          <pre><code>{detailItem.content}</code></pre>
                        ) : (
                          <div dangerouslySetInnerHTML={{ __html: sanitize(marked.parse(detailItem.content || '') as string) }} />
                        )}
                      </div>
                    </div>
                  )}

                  {/* Cross References: GitHub Repos + Referenced By */}
                  {(crossRefs.github_repos.length > 0 || crossRefs.related_items.length > 0) && (
                    <div style={{ marginTop: '24px', paddingTop: '20px', borderTop: '1px solid var(--border)' }}>
                      {crossRefs.github_repos.length > 0 && (
                        <div style={{ marginBottom: '16px' }}>
                          <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)', marginBottom: '10px', fontWeight: 600 }}>
                            ⭐ 关联 GitHub 仓库
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                            {crossRefs.github_repos.map((repo: GitHubRepo) => (
                              <button key={repo.item_id} onClick={() => {
                                // 加载该 GitHub repo 的详情
                                fetch(`/api/github/stars/${repo.item_id}`).then(r => r.json()).then(d => {
                                  if (d.ok !== false) setDetailItem(d);
                                });
                              }} style={{
                                display: 'inline-flex', alignItems: 'center', gap: '6px',
                                padding: '6px 12px', borderRadius: '8px',
                                background: 'var(--surface2)', border: '1px solid var(--border)',
                                color: 'var(--text)', cursor: 'pointer', fontSize: '0.82rem',
                              }}>
                                <span style={{ fontWeight: 600 }}>{repo.full_name}</span>
                                <span style={{ color: '#e3b341' }}>⭐ {(repo.stars || 0).toLocaleString()}</span>
                                <span style={{ color: 'var(--text-dim)' }}>{repo.language}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {crossRefs.related_items.length > 0 && (
                        <div>
                          <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)', marginBottom: '10px', fontWeight: 600 }}>
                            🔗 引用此内容的知识项
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '8px' }}>
                            {crossRefs.related_items.map((ref: Item) => (
                              <div key={ref.id} className="item-card" style={{ padding: '10px', minHeight: 'auto', cursor: 'pointer' }}
                                onClick={() => {
                                  fetch(`/api/items/${ref.id}`).then(r => r.json()).then(d => setDetailItem(d));
                                }}>
                                <div style={{ fontSize: '0.85rem', fontWeight: 500, marginBottom: '4px' }}>{ref.title}</div>
                                <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>{ref.summary}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Semantic Network Connections Graph / Related Items */}
                  {relatedItems.length > 0 && (
                    <div style={{ marginTop: '32px', paddingTop: '20px', borderTop: '1px solid var(--border)' }}>
                      <div style={{ fontSize: '0.9rem', color: 'var(--text-dim)', marginBottom: '16px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Search size={16} /> 语义聚类宇宙 (Semantic Network)
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '12px' }}>
                        {relatedItems.map((rel: Item) => (
                          <div
                            key={rel.id}
                            className="item-card"
                            style={{ padding: '12px', minHeight: 'auto' }}
                            onClick={(e) => { e.stopPropagation(); setDetailItem(rel); }}
                          >
                            <div style={{ fontSize: '0.9rem', fontWeight: 500, marginBottom: '6px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{rel.title || '无标题'}</div>
                            <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{rel.summary}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Collection header or Space Selector Tabs */}
        {view === 'collection' && activeCollection ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
            <button className="icon-btn" onClick={() => { setActiveCollection(''); setView('all'); }} title="返回"><ArrowLeft size={18} /></button>
            <h2 style={{ fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
              {collectionsList.find((c: Collection) => c.id === activeCollection)?.icon || '📁'} {collectionsList.find((c: Collection) => c.id === activeCollection)?.name || '收藏集'}
            </h2>
            <button className="btn" style={{ marginLeft: 'auto', fontSize: '0.8rem', padding: '4px 12px', color: 'var(--red)' }}
              onClick={() => setConfirmDialog({
                isOpen: true, msg: `确定删除收藏集「${collectionsList.find((c: Collection) => c.id === activeCollection)?.name}」吗？条目本身不会被删除。`,
                onConfirm: async () => {
                  await fetch(`/api/collections/${activeCollection}`, { method: 'DELETE' });
                  setActiveCollection(''); setView('all'); setConfirmDialog(null);
                  showToast('收藏集已删除', 'success');
                  fetch('/api/collections').then(r => r.json()).then(d => setCollectionsList(d.collections || [])).catch(() => {});
                }
              })}>删除收藏集</button>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', overflowX: 'auto', paddingBottom: '4px' }}>
            {[
              { id: 'all', label: '🌌 全部宇宙' },
              { id: 'default', label: '📦 默认区' },
              { id: 'work', label: '💼 工作区' },
              { id: 'ideas', label: '💡 灵感区' },
              { id: 'archive', label: '🧊 冷藏库' }
            ].map(s => (
              <button
                key={s.id}
                className={`btn ${space === s.id ? 'btn-primary' : ''}`}
                onClick={() => setSpace(s.id)}
                style={{ borderRadius: '20px', padding: '6px 16px', fontSize: '0.9rem', whiteSpace: 'nowrap' }}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}

        {view !== 'collection' && <div
          className="quick-input-zone"
          style={{ position: 'relative', overflow: 'hidden' }}
          onDragOver={e => e.preventDefault()}
          onDrop={e => { e.preventDefault(); uploadFiles(e.dataTransfer.files); }}
        >
          {isUploading && (
            <div style={{ position: 'absolute', inset: 0, background: 'var(--surface)', backdropFilter: 'blur(12px)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', zIndex: 10 }}>
              <div className="terminal-container" style={{ width: '100%', background: 'var(--surface2)', borderRadius: '12px', border: '1px solid var(--border)', padding: '20px', color: 'var(--accent)', fontFamily: 'monospace', fontSize: '0.85rem', height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', boxShadow: 'var(--shadow)' }}>
                <div style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)', paddingBottom: '12px', marginBottom: '8px', fontSize: '0.75rem', letterSpacing: '1px' }}>LANDROP CORE HYPER-ENGINE 1.0.0 (SSE LINKED)</div>
                {uploadLogs.length === 0 && <div style={{ color: 'var(--text-muted)' }}>$ 等待唤醒核心进程...</div>}
                {uploadLogs.map((log, i) => (
                  <div key={i} style={{ lineHeight: '1.4' }}><span style={{ color: 'var(--accent2)' }}>➜ </span> <span style={{ color: 'var(--text)' }}>{log}</span></div>
                ))}
                <div className="loading-dots" style={{ marginTop: 'auto', color: 'var(--accent2)', fontSize: '0.8rem' }}>Awaiting Sync Pipeline</div>
              </div>
            </div>
          )}

          <textarea
            className="quick-input-textarea"
            placeholder="在此直接粘贴文字、网页链接、代码，写下想法；或拖入文件上传（支持 PDF/Word/PPT/Excel/HTML/CSV/ZIP/图片/代码等）......"
            value={quickInput}
            onChange={e => setQuickInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (!e.shiftKey)) { e.preventDefault(); submitQuickAdd(); } }}
            disabled={isUploading}
          />
          <div className="quick-input-actions">
            <div className="left-actions" style={{ display: 'flex', gap: '10px' }}>
              <label className="upload-btn">
                <Upload size={16} /> 单文件传入
                <input type="file" multiple style={{ display: 'none' }} disabled={isUploading} onChange={e => uploadFiles(e.target.files)} />
              </label>
              <label className="upload-btn" title="批量导入并脱水整个知识图谱文件夹">
                <FolderDown size={16} /> 批量目录注入
                {/* @ts-ignore */}
                <input type="file" webkitdirectory="" directory="" multiple style={{ display: 'none' }} disabled={isUploading} onChange={e => uploadFolder(e.target.files)} />
              </label>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', color: autoRoute ? 'var(--accent)' : 'var(--text-dim)', cursor: 'pointer', marginLeft: '10px' }}>
                <input type="checkbox" checked={autoRoute} onChange={(e) => setAutoRoute(e.target.checked)} style={{ appearance: 'none', width: '14px', height: '14px', borderRadius: '3px', border: `1px solid ${autoRoute ? 'var(--accent)' : 'var(--text-dim)'}`, background: autoRoute ? 'var(--accent)' : 'transparent', cursor: 'pointer', position: 'relative' }} />
                🤖 智能分拣 (Auto-Routing)
              </label>
            </div>
            <div className="right-actions">
              <button className="btn btn-primary" disabled={isUploading} onClick={submitQuickAdd} style={{ padding: '6px 14px', fontSize: '0.85rem' }}>
                保存并由 AI 分析 (Enter)
              </button>
            </div>
          </div>
        </div>}

        <div className="items-grid">
          {items.map((item: Item) => (
            <div key={item.id} className="item-card" onClick={() => {
              if (item.type === 'github_external') {
                // 外部 GitHub 结果 → 点击收藏到 Stars
                const gh = item._gh_external;
                if (gh) {
                  fetch('/api/text', { method: 'POST', body: (() => { const fd = new FormData(); fd.append('content', gh.html_url || `https://github.com/${gh.full_name}`); return fd; })() })
                    .then(r => r.json())
                    .then(d => { if (d.ok) showToast(`已收藏 ${gh.full_name}`, 'success'); });
                }
              } else {
                setDetailItem(item);
              }
            }}>
              {item.type === 'github_external' ? (
                <span className="item-type-badge" style={{ background: 'linear-gradient(135deg, #238636, #2ea043)', color: '#fff' }}>GitHub</span>
              ) : (
                <span className={`item-type-badge badge-${item.type}`}>
                  {item.type === 'image' ? '图片' : item.type === 'code' ? '代码' : item.type === 'text' ? '文字' : item.type === 'github_star' ? '⭐' : (() => {
                    const ext = (item.title || '').split('.').pop()?.toLowerCase();
                    const fmtMap: Record<string, string> = { pdf: 'PDF', docx: 'Word', doc: 'Word', pptx: 'PPT', ppt: 'PPT', xlsx: 'Excel', xls: 'Excel', csv: 'CSV', html: 'HTML', htm: 'HTML', xml: 'XML', zip: 'ZIP', epub: 'EPub' };
                    return fmtMap[ext || ''] || '文件';
                  })()}
                </span>
              )}
              <div className="item-title">{item.type === 'github_external' ? item._gh_external?.full_name || item.title : item.title}</div>
              {item.type === 'github_external' && item._gh_external && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: '6px 0', fontSize: '0.78rem' }}>
                  <span style={{ color: '#e3b341' }}>⭐ {(item._gh_external.stars || 0).toLocaleString()}</span>
                  {item._gh_external.language && <span style={{ color: 'var(--text-dim)' }}>{item._gh_external.language}</span>}
                  <span style={{ color: 'var(--accent)', marginLeft: 'auto', fontSize: '0.75rem' }}>点击收藏</span>
                </div>
              )}
              <div className="item-summary">{item.summary}</div>

              {item.chunk_score !== undefined && (
                <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div className="match-score">🎯 精准片段命中 ({(item.chunk_score * 100).toFixed(0)}%)</div>
                </div>
              )}
              {item.match_chunk && (
                <div className="quote-block">
                  {item.match_chunk.length > 100 ? item.match_chunk.substring(0, 100) + '...' : item.match_chunk}
                </div>
              )}

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '10px' }}>
                {Array.isArray(item.tags) && item.tags.slice(0, 3).map((t: string) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px', fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                <span>{item.created_at?.slice(5, 16).replace('T', ' ')}</span>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button className="icon-btn" onClick={(e) => { e.stopPropagation(); setShowAddToCollection(item); }} title="添加到收藏集"><FolderPlus size={14} /></button>
                  {(item.type === 'file' || item.type === 'image') && (
                    <a href={`/api/download/${item.id}`} onClick={e => e.stopPropagation()} className="icon-btn" title="下载"><Download size={14} /></a>
                  )}
                  <button className="icon-btn" onClick={(e) => deleteItem(item.id, e)} title="删除"><Trash2 size={14} /></button>
                </div>
              </div>
            </div>
          ))}
          {items.length === 0 && (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
              还没有记录任何内容
            </div>
          )}
        </div>
        {hasMore && items.length > 0 && (
          <div style={{ textAlign: 'center', margin: '30px 0' }}>
            <button className="btn" onClick={loadMore}>加载更多内容</button>
          </div>
        )}
      </div>
    );

    if (!showKBLayout) return itemsView;

    // KB Layout: left sidebar + right content
    return (
      <div style={{ display: 'flex', gap: '16px', height: '100%', minHeight: 0 }}>
        {/* Left Sidebar — Spaces, Types, Collections */}
        <div style={{ width: 200, flexShrink: 0, overflowY: 'auto' }}>
          <div style={{ background: 'var(--surface)', borderRadius: '12px', border: '1px solid var(--border)', overflow: 'hidden' }}>
            {/* Spaces */}
            <div style={{ padding: '12px 16px', fontWeight: 700, fontSize: '0.85rem', borderBottom: '1px solid var(--border)' }}>空间</div>
            <div onClick={() => { setSpace('all'); setActiveCollection(''); setKbType(''); }}
              style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between',
                background: space === 'all' && !activeCollection && !kbType ? 'var(--accent-dim)' : 'transparent',
                color: space === 'all' && !activeCollection && !kbType ? 'var(--accent)' : 'var(--text2)' }}>
              <span>全部</span><span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{stats.total}</span>
            </div>
            {spacesList.map((s: Space) => (
              <div key={s.id} onClick={() => { setSpace(s.id); setActiveCollection(''); setKbType(''); }}
                style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between',
                  background: space === s.id && !activeCollection && !kbType ? 'var(--accent-dim)' : 'transparent',
                  color: space === s.id && !activeCollection && !kbType ? 'var(--accent)' : 'var(--text2)' }}>
                <span>{s.name}</span>
              </div>
            ))}

            {/* Types */}
            <div style={{ padding: '12px 16px', fontWeight: 700, fontSize: '0.85rem', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>类型</div>
            {[{id:'file',icon:'📁',label:'文件',count:stats.files},{id:'text',icon:'📝',label:'文字',count:stats.texts},{id:'code',icon:'💻',label:'代码',count:stats.codes}].map(t => (
              <div key={t.id} onClick={() => { setKbType(kbType === t.id ? '' : t.id); setActiveCollection(''); }}
                style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  background: kbType === t.id ? 'var(--accent-dim)' : 'transparent',
                  color: kbType === t.id ? 'var(--accent)' : 'var(--text2)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>{t.icon} {t.label}</span>
                <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{t.count}</span>
              </div>
            ))}

            {/* Collections */}
            <div style={{ padding: '12px 16px', fontWeight: 700, fontSize: '0.85rem', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>收藏集</span>
              <button onClick={(e) => { e.stopPropagation(); setShowCollectionModal(true); }}
                style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', padding: '2px' }} title="新建收藏集">
                <FolderPlus size={14} />
              </button>
            </div>
            {collectionsList.map((c: Collection) => (
              <div key={c.id} onClick={() => { setActiveCollection(c.id); setKbType(''); setSpace('all'); }}
                style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  background: activeCollection === c.id ? 'var(--accent-dim)' : 'transparent',
                  color: activeCollection === c.id ? 'var(--accent)' : 'var(--text2)' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '14px' }}>{c.icon || '📁'}</span> {c.name}
                </span>
                <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{c.item_count || 0}</span>
              </div>
            ))}
            {collectionsList.length === 0 && (
              <div style={{ padding: '8px 16px', fontSize: '0.78rem', color: 'var(--text-muted)' }}>还没有收藏集哦～</div>
            )}
          </div>
        </div>

        {/* Right Content */}
        {itemsView}
      </div>
    );
  };

  if (isLocked) {
    return <LockScreen onUnlock={() => { setIsLocked(false); refreshData(); setPage(1); }} />
  }

  return (
    <div className="app">
      {/* Toast Overlay */}
      {toast && (
        <div className={`toast ${toast.type}`}>
          {toast.msg}
        </div>
      )}

      {/* Custom Confirm Modal */}
      {confirmDialog && confirmDialog.isOpen && (
        <div className="modal-overlay active" style={{ zIndex: 10000 }} onClick={() => setConfirmDialog(null)}>
          <div className="item-card" style={{ width: '400px', display: 'flex', flexDirection: 'column', gap: '20px', padding: '30px' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: 0, color: 'var(--text)', fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: '8px' }}>确认操作</h3>
            <div style={{ color: 'var(--text-dim)', fontSize: '0.95rem', lineHeight: '1.6' }}>{confirmDialog.msg}</div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '10px' }}>
              <button className="btn" onClick={() => setConfirmDialog(null)}>取消</button>
              <button className="btn btn-primary" onClick={() => { confirmDialog.onConfirm(); setConfirmDialog(null); }}>确认</button>
            </div>
          </div>
        </div>
      )}

      {/* Create Collection Modal */}
      {showCollectionModal && (
        <div className="modal-overlay active" style={{ zIndex: 10000 }} onClick={() => setShowCollectionModal(false)}>
          <div className="item-card" style={{ width: '400px', display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <FolderPlus size={20} color="var(--accent)" /> 新建收藏集
            </h3>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
              <input value={newCollIcon} onChange={e => setNewCollIcon(e.target.value)} style={{ width: '48px', textAlign: 'center', fontSize: '24px', padding: '6px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)' }} />
              <input value={newCollName} onChange={e => setNewCollName(e.target.value)} placeholder="收藏集名称" autoFocus onKeyDown={e => { if (e.key === 'Enter') { /* create */ } }} style={{ flex: 1, padding: '10px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', fontSize: '0.95rem' }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button className="btn" onClick={() => setShowCollectionModal(false)}>取消</button>
              <button className="btn btn-primary" onClick={async () => {
                if (!newCollName.trim()) return;
                const res = await fetch('/api/collections', { method: 'POST', body: JSON.stringify({ name: newCollName, icon: newCollIcon || '📁' }), headers: { 'Content-Type': 'application/json' } });
                const d = await res.json();
                if (d.ok) {
                  setCollectionsList(prev => [{ id: d.id, name: newCollName, description: '', icon: newCollIcon || '📁', created_at: '', updated_at: '', item_count: 0 }, ...prev]);
                  setNewCollName('');
                  setNewCollIcon('📁');
                  setShowCollectionModal(false);
                  showToast('收藏集创建成功', 'success');
                }
              }}>创建</button>
            </div>
          </div>
        </div>
      )}

      {/* Add to Collection Modal */}
      {showAddToCollection && (
        <div className="modal-overlay active" style={{ zIndex: 10000 }} onClick={() => setShowAddToCollection(null)}>
          <div className="item-card" style={{ width: '400px', display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Folder size={20} color="var(--accent)" /> 添加到收藏集
            </h3>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>选择要添加「{showAddToCollection.title || '未命名'}」的收藏集：</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '300px', overflowY: 'auto' }}>
              {collectionsList.map((c: Collection) => (
                <button key={c.id} className="btn" style={{ justifyContent: 'flex-start', padding: '10px 14px', fontSize: '0.9rem', background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)' }}
                  onClick={async () => {
                    await fetch(`/api/collections/${c.id}/items`, { method: 'POST', body: JSON.stringify({ item_ids: [showAddToCollection.id] }), headers: { 'Content-Type': 'application/json' } });
                    setShowAddToCollection(null);
                    showToast(`已添加到 ${c.name}`, 'success');
                    fetch('/api/collections').then(r => r.json()).then(d => setCollectionsList(d.collections || [])).catch(() => {});
                  }}>
                  {c.icon || '📁'} {c.name} <span style={{ marginLeft: 'auto', color: 'var(--text-muted)', fontSize: '0.8rem' }}>{c.item_count} 条</span>
                </button>
              ))}
              {collectionsList.length === 0 && <div style={{ padding: '20px', textAlign: 'center', color: 'var(--text-muted)' }}>还没有收藏集哦～，请先创建</div>}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setShowAddToCollection(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* Mobile Overlay */}
      {sidebarOpen && <div className="modal-overlay active" style={{ zIndex: 190 }} onClick={() => setSidebarOpen(false)}></div>}

      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div className="logo">KnowHub</div>
            <div className="logo-sub">AI-Powered Hub</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          <SidebarItem icon={<Database size={18} />} label="知识库" active={view === 'kb'} count={stats.total} onClick={() => { setView('kb'); setDetailItem(null); }} />
          <SidebarItem icon={<Star size={18} />} label="GitHub Stars" active={view === 'github'} onClick={() => { setView('github'); setDetailItem(null); }} />

          <div style={{ marginTop: '12px', borderTop: '1px solid var(--border)', paddingTop: '14px' }}>
            <SidebarItem icon={<Wrench size={18} />} label="工具箱" active={['ai','graph','gallery'].includes(view)} onClick={() => { setView('tools'); setDetailItem(null); }} />
            {(view === 'tools' || view === 'ai' || view === 'graph' || view === 'gallery') && (
              <>
                <SidebarItem icon={<MessageSquare size={16} />} label="AI 问答" active={view === 'ai' || (view === 'tools' && toolsTab === 'ai')} indent onClick={() => { setView('ai'); setToolsTab('ai'); setDetailItem(null); }} />
                <SidebarItem icon={<Zap size={16} />} label="知识图谱" active={view === 'graph' || (view === 'tools' && toolsTab === 'graph')} indent onClick={() => { setView('graph'); setToolsTab('graph'); setDetailItem(null); }} />
                <SidebarItem icon={<ImageIcon size={16} />} label="媒体画廊" active={view === 'gallery' || (view === 'tools' && toolsTab === 'gallery')} indent onClick={() => { setView('gallery'); setToolsTab('gallery'); setDetailItem(null); }} />
              </>
            )}
          </div>

          <div style={{ marginTop: '12px', borderTop: '1px solid var(--border)', paddingTop: '14px' }}>
            <SidebarItem icon={<Settings size={18} />} label="系统设置" active={view === 'settings'} onClick={() => { setView('settings'); setDetailItem(null); }} />
          </div>
        </nav>

        <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}><span>存储容量</span><span>{(stats.total_size / 1024 / 1024).toFixed(2)} MB</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <button onClick={() => window.open('/api/export', '_blank')} className="btn" style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '8px', fontSize: '0.8rem', background: 'var(--surface3)', border: 'none', color: 'var(--text-dim)' }}>
              <FolderDown size={14} /> 导出为 Obsidian (Zip)
            </button>
            <button onClick={() => window.open('/api/backup', '_blank')} className="btn" style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '8px', fontSize: '0.8rem', background: 'var(--surface3)', border: 'none', color: 'var(--text-dim)' }}>
              <Database size={14} /> 备份整个大脑 (SQLite)
            </button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="header" style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
          <button className="icon-btn menu-btn" onClick={() => setSidebarOpen(true)}>
            <span style={{ fontSize: '18px', fontWeight: 'bold' }}>≡</span>
          </button>
          <div className="search-box cursor-text">
            <Search />
            <input
              type="text"
              placeholder={deepSearch ? "深度检索模式：AI 查询扩展 + LLM 重排..." : "搜索任何散落的笔记与代码..."}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  e.currentTarget.blur();
                }
              }}
            />
          </div>
          <button
            className={`icon-btn ${deepSearch ? 'deep-search-active' : ''}`}
            onClick={() => setDeepSearch(d => !d)}
            title={deepSearch ? '深度检索已开启 (LLM 重排)' : '点击开启深度检索'}
            style={{ flexShrink: 0, position: 'relative' }}
          >
            <Zap size={18} />
            {deepSearch && <span style={{ position: 'absolute', top: '-2px', right: '-2px', width: '8px', height: '8px', borderRadius: '50%', background: 'var(--green)' }} />}
          </button>
          <button className="icon-btn" onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')} title="切换色彩主题" style={{ flexShrink: 0 }}>
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </header>

        <div className="content">
          {renderContent()}
        </div>
      </div>
    </div>
  );
}

const SettingsView = ({ onSave, showToast }: { onSave: () => void, showToast: (msg: string, type: string) => void }) => {
  const [config, setConfig] = useState({ AI_API_KEY: '', AI_BASE_URL: '', AI_MODEL: '', SYSTEM_PASSWORD: '', VISION_API_KEY: '', VISION_BASE_URL: '', VISION_MODEL: '' });
  const [loading, setLoading] = useState(true);

  const [digest, setDigest] = useState<DigestConfig | null>(null);
  const [digestSaving, setDigestSaving] = useState(false);
  const [triggering, setTriggering] = useState('');

  useEffect(() => {
    fetch('/api/settings').then(r => r.json()).then(data => {
      setConfig({
        AI_API_KEY: data.AI_API_KEY || '',
        AI_BASE_URL: data.AI_BASE_URL || '',
        AI_MODEL: data.AI_MODEL || '',
        SYSTEM_PASSWORD: data.SYSTEM_PASSWORD || '',
        VISION_API_KEY: data.VISION_API_KEY || '',
        VISION_BASE_URL: data.VISION_BASE_URL || '',
        VISION_MODEL: data.VISION_MODEL || ''
      });
      setLoading(false);
    });
    fetch('/api/digest/config').then(r => r.json()).then(setDigest);
  }, []);

  const saveConfig = async () => {
    await fetch('/api/settings', { method: 'POST', body: JSON.stringify(config), headers: { 'Content-Type': 'application/json' } });
    onSave();
  };

  const saveDigestConfig = async () => {
    if (!digest) return;
    setDigestSaving(true);
    await fetch('/api/digest/config', { method: 'POST', body: JSON.stringify(digest), headers: { 'Content-Type': 'application/json' } });
    setDigestSaving(false);
  };

  const triggerReport = async (type: string) => {
    setTriggering(type);
    try {
      const res = await fetch('/api/digest/trigger', { method: 'POST', body: JSON.stringify({ type }), headers: { 'Content-Type': 'application/json' } });
      const data = await res.json();
      if (data.ok) showToast('报告已加入生成队列，完成后将推送至微信', 'success');
      else showToast('生成失败: ' + data.msg, 'error');
    } catch { showToast('网络错误', 'error'); }
    setTriggering('');
  };

  const dField = (key: string, fallback: number | string = 0): string | number => (digest?.[key as keyof DigestConfig] ?? fallback) as string | number;

  if (loading) return <div style={{ padding: '40px' }} className="loading-dots">加载配置中</div>;

  const cardStyle: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: '16px', padding: '24px' };
  const labelStyle: React.CSSProperties = { fontSize: '0.85rem', color: 'var(--text-dim)', fontWeight: 600 };
  const inputStyle: React.CSSProperties = { margin: 0, background: 'var(--surface2)' };
  const selectStyle: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 10px', fontSize: '0.8rem', color: 'var(--text)' };
  const sectionTitle: React.CSSProperties = { fontSize: '1rem', fontWeight: 'bold', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' };

  const hourOptions = Array.from({ length: 24 }, (_, i) => <option key={i} value={i}>{String(i).padStart(2, '0')}:00</option>);
  const dayOptions = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((d, i) => <option key={i} value={i}>{d}</option>);

  // Helper: one report row (checkbox + selects + trigger button)
  const ReportRow = ({ label, enabledKey, hourKey, triggerType, lastKey, dayKey, hasDay, dayLabel }: {
    label: string; enabledKey: string; hourKey: string; triggerType: string;
    lastKey: string; dayKey?: string; hasDay?: boolean; dayLabel?: string;
  }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem', minWidth: '60px', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!dField(enabledKey)} onChange={e => setDigest({ ...digest, [enabledKey]: e.target.checked ? 1 : 0 } as DigestConfig)} />
        <span style={{ fontWeight: dField(enabledKey) ? 600 : 400 }}>{label}</span>
      </label>
      {hasDay && dayKey && (
        <select value={dField(dayKey, 1)} onChange={e => setDigest({ ...digest, [dayKey]: +e.target.value } as DigestConfig)} style={selectStyle}>
          {dayLabel === '每月' ? Array.from({ length: 28 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}号</option>) : dayOptions}
        </select>
      )}
      <select value={dField(hourKey, 9)} onChange={e => setDigest({ ...digest, [hourKey]: +e.target.value } as DigestConfig)} style={selectStyle}>{hourOptions}</select>
      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', flex: 1 }}>{dField(lastKey) ? `上次: ${String(dField(lastKey)).slice(0, 16)}` : '从未生成'}</span>
      <button className="btn" disabled={!!triggering} onClick={() => triggerReport(triggerType)} style={{ padding: '5px 12px', fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
        {triggering === triggerType ? '生成中...' : '立即生成'}
      </button>
    </div>
  );

  return (
    <div className="detail-view" style={{ maxWidth: '860px', margin: '0 auto', width: '100%', display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '1.3rem' }}>
        <Settings size={24} color="var(--accent)" /> 系统设置
      </h2>

      {/* ── AI 配置 ── */}
      <div className="item-card" style={cardStyle}>
        <div style={sectionTitle}>🧠 AI 模型配置</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={labelStyle}>API Key</label>
            <input className="title-input" style={inputStyle} value={config.AI_API_KEY} onChange={e => setConfig({ ...config, AI_API_KEY: e.target.value })} placeholder="sk-..." type="password" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={labelStyle}>Base URL</label>
            <input className="title-input" style={inputStyle} value={config.AI_BASE_URL} onChange={e => setConfig({ ...config, AI_BASE_URL: e.target.value })} placeholder="https://api.deepseek.com" />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <label style={labelStyle}>模型名称</label>
          <input className="title-input" style={inputStyle} value={config.AI_MODEL} onChange={e => setConfig({ ...config, AI_MODEL: e.target.value })} placeholder="deepseek-chat" />
        </div>
      </div>

      {/* ── Vision 配置 ── */}
      <div className="item-card" style={cardStyle}>
        <div style={sectionTitle}>👁️ Vision 多模态通道 <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 400 }}>（可选，留空则使用本地 OCR）</span></div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={labelStyle}>Vision API Key</label>
            <input className="title-input" style={inputStyle} value={config.VISION_API_KEY} onChange={e => setConfig({ ...config, VISION_API_KEY: e.target.value })} placeholder="sk-..." type="password" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label style={labelStyle}>Vision Base URL</label>
            <input className="title-input" style={inputStyle} value={config.VISION_BASE_URL} onChange={e => setConfig({ ...config, VISION_BASE_URL: e.target.value })} placeholder="https://api.openai.com/v1" />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <label style={labelStyle}>Vision 模型</label>
          <input className="title-input" style={inputStyle} value={config.VISION_MODEL} onChange={e => setConfig({ ...config, VISION_MODEL: e.target.value })} placeholder="gpt-4o, claude-3-5-sonnet, qwen-vl-max" />
        </div>
      </div>

      {/* ── 安全 ── */}
      <div className="item-card" style={cardStyle}>
        <div style={sectionTitle}>🔒 安全</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxWidth: '400px' }}>
          <label style={labelStyle}>访问口令</label>
          <input className="title-input" style={{ ...inputStyle, background: 'rgba(255,0,0,0.04)' }} value={config.SYSTEM_PASSWORD} onChange={e => setConfig({ ...config, SYSTEM_PASSWORD: e.target.value })} placeholder="留空即开放访问" type="password" />
        </div>
        <button className="btn btn-primary" onClick={saveConfig} style={{ padding: '10px 24px', fontSize: '0.95rem' }}>保存核心配置</button>
      </div>

      {/* ── 知识库简报 ── */}
      {digest && (
        <div className="item-card" style={cardStyle}>
          <div style={sectionTitle}>📰 知识库简报</div>
          <ReportRow label="日报" enabledKey="daily_enabled" hourKey="daily_hour" triggerType="kb_daily" lastKey="last_daily" />
          <ReportRow label="周报" enabledKey="weekly_enabled" hourKey="weekly_hour" triggerType="kb_weekly" lastKey="last_weekly" hasDay dayKey="weekly_day" />
        </div>
      )}

      {/* ── GitHub Stars 报告 ── */}
      {digest && (
        <div className="item-card" style={cardStyle}>
          <div style={sectionTitle}>⭐ GitHub Stars 报告</div>
          <ReportRow label="日报" enabledKey="gh_stars_daily_enabled" hourKey="gh_stars_daily_hour" triggerType="gh_stars_daily" lastKey="last_gh_stars_daily" />
          <ReportRow label="周报" enabledKey="gh_stars_weekly_enabled" hourKey="gh_stars_weekly_hour" triggerType="gh_stars_weekly" lastKey="last_gh_stars_weekly" hasDay dayKey="gh_stars_weekly_day" />
        </div>
      )}

      {/* ── GitHub Trending 报告 ── */}
      {digest && (
        <div className="item-card" style={cardStyle}>
          <div style={sectionTitle}>🔥 GitHub Trending 报告</div>
          <ReportRow label="每日" enabledKey="gh_trending_daily_enabled" hourKey="gh_trending_daily_hour" triggerType="gh_trending_daily" lastKey="last_gh_trending_daily" />
          <ReportRow label="每周" enabledKey="gh_trending_weekly_enabled" hourKey="gh_trending_weekly_hour" triggerType="gh_trending_weekly" lastKey="last_gh_trending_weekly" hasDay dayKey="gh_trending_weekly_day" />
          <ReportRow label="每月" enabledKey="gh_trending_monthly_enabled" hourKey="gh_trending_monthly_hour" triggerType="gh_trending_monthly" lastKey="last_gh_trending_monthly" hasDay dayKey="gh_trending_monthly_day" dayLabel="每月" />
          <button className="btn btn-primary" onClick={saveDigestConfig} disabled={digestSaving} style={{ padding: '10px 24px', fontSize: '0.95rem', marginTop: '4px' }}>
            {digestSaving ? '保存中...' : '保存报告调度配置'}
          </button>
        </div>
      )}
    </div>
  );
};

function SidebarItem({ icon, label, count, active, onClick, indent }: {
  icon: React.ReactNode; label: string; count?: number; active: boolean;
  onClick: () => void; indent?: boolean;
}) {
  return (
    <div className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}
      style={indent ? { paddingLeft: '36px', fontSize: '0.85rem' } : undefined}>
      {icon}
      <span>{label}</span>
      {count !== undefined && <span className="nav-badge">{count}</span>}
    </div>
  )
}

const LockScreen = ({ onUnlock }: { onUnlock: () => void }) => {
  const [pwd, setPwd] = useState('');
  const [err, setErr] = useState('');
  const submit = async () => {
    if (!pwd) return;
    const res = await fetch('/api/login', { method: 'POST', body: JSON.stringify({ password: pwd }), headers: { 'Content-Type': 'application/json' } });
    if (res.ok) { onUnlock(); }
    else { setErr('密码不对哦，再试试～'); }
  }
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, background: 'var(--bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="item-card" style={{ width: '400px', display: 'flex', flexDirection: 'column', gap: '20px', alignItems: 'center', padding: '40px' }}>
        <div style={{ padding: '16px', background: 'rgba(139, 92, 246, 0.1)', borderRadius: '50%', color: '#8b5cf6' }}><Lock size={40} /></div>
        <h2 style={{ letterSpacing: '2px', fontFamily: 'JetBrains Mono, monospace' }}>KnowHub</h2>
        <div style={{ color: 'var(--text-dim)', fontSize: '0.9rem' }}>输入密码进入你的知识空间～</div>
        <input type="password" autoFocus className="title-input" style={{ background: 'var(--surface2)', textAlign: 'center', letterSpacing: '8px' }} value={pwd} onChange={e => setPwd(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submit(); }} placeholder="••••••••" />
        {err && <div style={{ color: 'var(--red)', fontSize: '0.85rem' }}>{err}</div>}
        <button className="btn btn-primary" onClick={submit} style={{ width: '100%', padding: '12px' }}>进入</button>
      </div>
    </div>
  );
}
const KnowledgeGraphView = ({ theme, onNodeClick }: { theme: string, onNodeClick: (node: { id: string; name: string; group: string; val: number }) => void }) => {
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] });
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const updateSize = () => {
      const container = document.getElementById('graph-container');
      if (container) {
        setDimensions({ width: container.clientWidth, height: container.clientHeight });
      }
    };
    window.addEventListener('resize', updateSize);
    updateSize();

    fetch('/api/graph').then(r => r.json()).then(data => {
      setGraphData(data);
      setIsLoading(false);
      setTimeout(updateSize, 100);
    }).catch(e => {
      console.error(e);
      setIsLoading(false);
    });

    return () => window.removeEventListener('resize', updateSize);
  }, []);

  if (isLoading) {
    return <div style={{ height: 'calc(100vh - 100px)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} className="loading-dots">计算全连接星图关联中 (VLM & Vector Matrix)...</div>;
  }

  const isDark = theme === 'dark';
  const bgColor = isDark ? '#09090b' : '#f8f9fa';

  const colorMap: Record<string, string> = {
    'default': isDark ? '#aaaaaa' : '#666666',
    'work': isDark ? '#3b82f6' : '#2563eb',
    'ideas': isDark ? '#eab308' : '#ca8a04',
    'archive': isDark ? '#6b7280' : '#4b5563',
  };

  return (
    <div id="graph-container" style={{ height: 'calc(100vh - 80px)', width: '100%', borderRadius: '12px', overflow: 'hidden', border: `1px solid var(--border)`, background: bgColor }}>
      <ForceGraph2D
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        nodeLabel="name"
        nodeColor={node => colorMap[node.group] || colorMap['default']}
        nodeRelSize={6}
        nodeVal={node => node.val || 1}
        linkColor={() => isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.1)'}
        linkWidth={link => (link.value as number) * 2}
        onNodeClick={onNodeClick}
        backgroundColor={bgColor}
      />
    </div>
  );
};

const GalleryView = ({ space, spacesList, onSpaceChange, onOpenItem }: { space: string, spacesList: Space[], onSpaceChange: (s: string) => void, onOpenItem: (item: Item) => void }) => {
  const [galleryType, setGalleryType] = useState<'image' | 'file'>('image');
  const [galleryItems, setGalleryItems] = useState<Item[]>([]);
  const [galleryTotal, setGalleryTotal] = useState(0);
  const [galleryLoading, setGalleryLoading] = useState(true);
  const [galleryPage, setGalleryPage] = useState(1);
  const [previewItem, setPreviewItem] = useState<Item | null>(null);

  useEffect(() => {
    setGalleryPage(1);
    loadGallery(1);
  }, [galleryType, space]);

  const loadGallery = async (page: number) => {
    setGalleryLoading(true);
    try {
      const res = await fetch(`/api/gallery?type=${galleryType}&space=${space}&page=${page}&page_size=50`);
      const data = await res.json();
      if (page === 1) setGalleryItems(data.items || []);
      else setGalleryItems(prev => [...prev, ...(data.items || [])]);
      setGalleryTotal(data.total || 0);
    } catch { }
    setGalleryLoading(false);
  };

  const loadMore = () => {
    const next = galleryPage + 1;
    setGalleryPage(next);
    loadGallery(next);
  };

  return (
    <div style={{ width: '100%' }}>
      {/* Gallery Header */}
      <div style={{ display: 'flex', gap: '12px', marginBottom: '20px', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: '4px', background: 'var(--surface2)', borderRadius: '8px', padding: '4px' }}>
          <button
            className={`btn ${galleryType === 'image' ? 'btn-primary' : ''}`}
            onClick={() => setGalleryType('image')}
            style={{ borderRadius: '6px', padding: '6px 16px', fontSize: '0.85rem' }}
          >
            <ImageIcon size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />图片画廊
          </button>
          <button
            className={`btn ${galleryType === 'file' ? 'btn-primary' : ''}`}
            onClick={() => setGalleryType('file')}
            style={{ borderRadius: '6px', padding: '6px 16px', fontSize: '0.85rem' }}
          >
            <FileText size={14} style={{ marginRight: '6px', verticalAlign: 'middle' }} />文件列表
          </button>
        </div>
        <select
          value={space}
          onChange={e => onSpaceChange(e.target.value)}
          style={{ padding: '6px 12px', borderRadius: '6px', background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)', fontSize: '0.85rem' }}
        >
          <option value="all">🌌 全部空间</option>
          {spacesList.map((s: Space) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          共 {galleryTotal} 个{galleryType === 'image' ? '图片' : '文件'}
        </span>
      </div>

      {/* Image Gallery Grid */}
      {galleryType === 'image' && (
        <div className="gallery-grid">
          {galleryItems.map(item => (
            <div key={item.id} className="gallery-item" onClick={() => setPreviewItem(item)}>
              {item.has_file ? (
                <img src={`/api/download/${item.id}`} alt={item.title} loading="lazy" />
              ) : (
                <div style={{ width: '100%', height: '200px', background: 'var(--surface2)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                  <ImageIcon size={32} />
                </div>
              )}
              <div className="gallery-item-info">
                <div className="gallery-item-title">{item.title || '未命名'}</div>
                <div className="gallery-item-meta">{item.created_at?.slice(0, 10)}</div>
              </div>
            </div>
          ))}
          {galleryItems.length === 0 && !galleryLoading && (
            <div style={{ gridColumn: '1 / -1', textAlign: 'center', padding: '60px 20px', color: 'var(--text-muted)' }}>
              还没有图片呢
            </div>
          )}
        </div>
      )}

      {/* File List Table */}
      {galleryType === 'file' && (
        <div className="file-list-table">
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                <th style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 600 }}>文件名</th>
                <th style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 600, width: '100px' }}>大小</th>
                <th style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 600, width: '100px' }}>类型</th>
                <th style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 600, width: '140px' }}>日期</th>
                <th style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)', fontWeight: 600, width: '80px' }}></th>
              </tr>
            </thead>
            <tbody>
              {galleryItems.map(item => (
                <tr key={item.id} className="file-list-row" onClick={() => onOpenItem(item)}>
                  <td style={{ padding: '10px 12px', fontSize: '0.9rem', cursor: 'pointer' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <FileText size={16} color="var(--text-muted)" />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '300px' }}>{item.title || '未命名'}</span>
                    </div>
                  </td>
                  <td style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {item.file_size ? `${(item.file_size / 1024).toFixed(1)} KB` : '-'}
                  </td>
                  <td style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {item.mime_type?.split('/')[1]?.toUpperCase() || '-'}
                  </td>
                  <td style={{ padding: '10px 12px', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {item.created_at?.slice(0, 16).replace('T', ' ')}
                  </td>
                  <td style={{ padding: '10px 12px' }}>
                    {item.has_file && (
                      <a href={`/api/download/${item.id}`} onClick={e => e.stopPropagation()} className="icon-btn" title="下载">
                        <Download size={14} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
              {galleryItems.length === 0 && !galleryLoading && (
                <tr>
                  <td colSpan={5} style={{ padding: '60px 20px', textAlign: 'center', color: 'var(--text-muted)' }}>还没有文件呢</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Load More */}
      {galleryItems.length < galleryTotal && (
        <div style={{ textAlign: 'center', margin: '30px 0' }}>
          <button className="btn" onClick={loadMore}>加载更多</button>
        </div>
      )}

      {galleryLoading && (
        <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }} className="loading-dots">加载中</div>
      )}

      {/* Image Preview Modal */}
      {previewItem && (
        <div className="modal-overlay active" onClick={() => setPreviewItem(null)} style={{ zIndex: 10000 }}>
          <div style={{ maxWidth: '90vw', maxHeight: '90vh', position: 'relative' }} onClick={e => e.stopPropagation()}>
            <img
              src={`/api/download/${previewItem.id}`}
              alt={previewItem.title}
              style={{ maxWidth: '90vw', maxHeight: '85vh', objectFit: 'contain', borderRadius: '8px' }}
            />
            <div style={{ textAlign: 'center', marginTop: '12px', color: '#fff', fontSize: '0.9rem', textShadow: '0 1px 4px rgba(0,0,0,0.5)' }}>
              {previewItem.title || '未命名'}
            </div>
            <button
              onClick={() => setPreviewItem(null)}
              style={{ position: 'absolute', top: '-40px', right: 0, background: 'rgba(255,255,255,0.2)', border: 'none', color: '#fff', borderRadius: '50%', width: '32px', height: '32px', cursor: 'pointer', fontSize: '18px' }}
            >
              ✕
            </button>
            <a
              href={`/api/download/${previewItem.id}`}
              style={{ position: 'absolute', top: '-40px', right: '44px', background: 'rgba(255,255,255,0.2)', border: 'none', color: '#fff', borderRadius: '50%', width: '32px', height: '32px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', textDecoration: 'none' }}
              title="下载原图"
            >
              <Download size={16} />
            </a>
          </div>
        </div>
      )}
    </div>
  );
};

// ── GitHub Stars Language Colors ──────────────────────────────────────────
const LANG_COLORS: Record<string, string> = {
  JavaScript: '#f1e05a', TypeScript: '#3178c6', Python: '#3572A5', Java: '#b07219',
  Go: '#00ADD8', Rust: '#dea584', C: '#555555', 'C++': '#f34b7d', 'C#': '#178600',
  Ruby: '#701516', PHP: '#4F5D95', Swift: '#F05138', Kotlin: '#A97BFF',
  Dart: '#00B4AB', Scala: '#c22d40', Lua: '#000080', Shell: '#89e051',
  HTML: '#e34c26', CSS: '#563d7c', Vue: '#41b883', Svelte: '#ff3e00',
  Zig: '#ec915c', Nim: '#ffc200', Elixir: '#6e4a7e', Haskell: '#5e5086',
  R: '#198CE7', Julia: '#a270ba', Perl: '#0298c3', Vim: '#199f4b',
};

// ── Star Button with state transition ───────────────────────────────────
const StarButton = ({ fullName, accounts, starRepo, compact }: { fullName: string, accounts: GitHubAccount[], starRepo: (n: string, a: 'star'|'unstar', i?: number) => Promise<boolean>, compact?: boolean }) => {
  const [state, setState] = useState<'idle'|'picking'|'loading'|'done'>('idle');

  const doStar = async (idx: number) => {
    setState('loading');
    const ok = await starRepo(fullName, 'star', idx);
    setState(ok ? 'done' : 'idle');
    if (ok) setTimeout(() => setState('idle'), 2000);
  };

  const s = compact
    ? { p: '4px 10px', r: '6px', fs: '0.78rem' }
    : { p: '8px 14px', r: '8px', fs: '0.85rem' };

  if (state === 'done') return (
    <button style={{ padding: s.p, borderRadius: s.r, border: '1px solid var(--green)', background: 'var(--green)15', color: 'var(--green)', cursor: 'default', fontSize: s.fs, fontWeight: 500 }}>
      ✓ Starred
    </button>
  );

  if (state === 'loading') return (
    <button style={{ padding: s.p, borderRadius: s.r, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'wait', fontSize: s.fs, fontWeight: 500 }}>
      ...
    </button>
  );

  const enabled = accounts.filter((a: GitHubAccount) => a.enabled !== false);

  return (
    <div style={{ position: 'relative' }}>
      <button onClick={e => { e.stopPropagation(); if (enabled.length <= 1) { doStar(0); } else { setState('picking'); } }}
        style={{ padding: s.p, borderRadius: s.r, border: '1px solid var(--border)', background: 'transparent', color: '#f59e0b', cursor: 'pointer', fontSize: s.fs, fontWeight: 500 }}>
        ⭐ Star
      </button>
      {state === 'picking' && (
        <div onClick={e => e.stopPropagation()} style={{ position: 'absolute', bottom: compact ? '100%' : undefined, top: compact ? undefined : '100%', left: 0, marginTop: compact ? undefined : '4px', marginBottom: compact ? '4px' : undefined, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', padding: '6px', zIndex: 50, boxShadow: '0 4px 16px rgba(0,0,0,0.3)', minWidth: '150px' }}>
          <div style={{ fontSize: '0.7rem', color: 'var(--text2)', padding: '2px 6px', marginBottom: '4px' }}>选择账号</div>
          {enabled.map((acc: GitHubAccount, i: number) => (
            <div key={i} onClick={() => doStar(i)}
              style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 8px', borderRadius: '6px', cursor: 'pointer', fontSize: '0.78rem', color: 'var(--text)' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface2)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
              <img src={acc.avatar_url || ''} alt="" style={{ width: 18, height: 18, borderRadius: '50%' }} />
              {acc.login || acc.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Unstar Button (two-click confirm) ──────────────────────────────────
const UnstarButton = ({ fullName, onDone, starRepo }: { fullName: string, onDone: () => void, starRepo: (n: string, a: 'star'|'unstar') => Promise<boolean> }) => {
  const [confirming, setConfirming] = useState(false);
  return (
    <button onClick={async () => {
      if (!confirming) { setConfirming(true); return; }
      const ok = await starRepo(fullName, 'unstar');
      if (ok) onDone();
      setConfirming(false);
    }}
      style={{ padding: '8px', borderRadius: '8px', border: `1px solid ${confirming ? 'var(--red)' : 'var(--border)'}`, background: confirming ? 'var(--red)' : 'transparent', color: confirming ? '#fff' : 'var(--text2)', cursor: 'pointer', fontSize: '0.8rem', transition: 'all 0.15s' }}>
      {confirming ? '确认 Unstar?' : 'Unstar'}
    </button>
  );
};

// ── GitHub Stars View ────────────────────────────────────────────────────
const GitHubStarsView = ({ showToast }: { showToast: (msg: string, type?: string) => void }) => {
  const [currentView, setCurrentView] = useState<'repositories'|'releases'|'discover'|'settings'>('repositories');
  const [items, setItems] = useState<GitHubRepo[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [language, setLanguage] = useState('');
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState('recently');
  const [languages, setLanguages] = useState<string[]>([]);
  const [categories, setCategories] = useState<GitHubCategory[]>([]);
  const [detailRepo, setDetailRepo] = useState<GitHubRepo | null>(null);
  const [releases, setReleases] = useState<GitHubRelease[]>([]);
  const [trendingItems, setTrendingItems] = useState<TrendingItem[]>([]);
  const [discoverChannel, setDiscoverChannel] = useState('trending');
  const [syncing, setSyncing] = useState(false);
  const [ghAccounts, setGhAccounts] = useState<GitHubAccount[]>([]);
  const [showConfig, setShowConfig] = useState(false);
  const [tokenInput, setTokenInput] = useState('');
  const [unreadCount, setUnreadCount] = useState(0);
  const [detailTab, setDetailTab] = useState<'info'|'releases'|'readme'>('info');
  const [showCatModal, setShowCatModal] = useState(false);
  const [catName, setCatName] = useState('');
  const [catKeywords, setCatKeywords] = useState('');
  const [catColor, setCatColor] = useState('#8b5cf6');
  const [catIcon, setCatIcon] = useState('📁');
  const [editingCat, setEditingCat] = useState<GitHubCategory | null>(null);
  const [readmeContent, setReadmeContent] = useState('');
  const [readmeLoading, setReadmeLoading] = useState(false);
  const [remoteReleases, setRemoteReleases] = useState<GitHubRelease[]>([]);
  const [remoteReleasesLoading, setRemoteReleasesLoading] = useState(false);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [discoverLang, setDiscoverLang] = useState('');
  const [discoverSince, setDiscoverSince] = useState('daily');
  const [topicInput, setTopicInput] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [releaseFilter, setReleaseFilter] = useState('');
  const [discoverDetail, setDiscoverDetail] = useState<TrendingItem | null>(null);
  const [discoverReadme, setDiscoverReadme] = useState('');
  const [discoverReadmeLoading, setDiscoverReadmeLoading] = useState(false);

  const [confirmDel, setConfirmDel] = useState('');

  const starRepo = async (fullName: string, action: 'star' | 'unstar' = 'star', accountIndex: number = 0) => {
    try {
      const r = await fetch('/api/github/star', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ full_name: fullName, action, account_index: accountIndex })
      });
      const d = await r.json();
      if (d.ok) {
        showToast(action === 'star' ? `⭐ Star 成功: ${fullName}` : `Unstar 成功: ${fullName}`, 'success');
        return true;
      } else {
        showToast(d.error || `${action} 失败`, 'error');
        return false;
      }
    } catch (e: unknown) {
      showToast(`${action} 失败: ${(e as Error).message || e}`, 'error');
      return false;
    }
  };
  const [ghChatOpen, setGhChatOpen] = useState(false);
  const [ghChatRepo, setGhChatRepo] = useState<(Partial<GitHubRepo> & { _releaseContext?: string; stargazers_count?: number; html_url?: string }) | null>(null);
  const [ghChatMsgs, setGhChatMsgs] = useState<{role: string, content: string}[]>([]);
  const [ghChatInput, setGhChatInput] = useState('');
  const [ghChatLoading, setGhChatLoading] = useState(false);
  const ghChatMsgsRef = useRef<HTMLDivElement>(null);
  const ghChatAutoScrollRef = useRef(true);

  const subscribedIds = new Set(subscriptions.map((s: Subscription) => s.item_id));

  useEffect(() => { checkConfig(); }, []);

  const checkConfig = async () => {
    try {
      const res = await fetch('/api/github/config');
      const data = await res.json();
      setGhAccounts(data.accounts || []);
      if (data.has_token) { loadStars(); loadCategories(); loadUnread(); loadTopTags(); }
    } catch {}
  };

  const loadStars = async (p = 1, q?: string) => {
    const s = q !== undefined ? q : search;
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: '30', sort });
      if (s) params.set('search', s);
      if (language) params.set('language', language);
      if (category) params.set('category', category);
      const res = await fetch(`/api/github/stars?${params}`);
      const data = await res.json();
      if (p === 1) setItems(data.items || []);
      else setItems(prev => [...prev, ...(data.items || [])]);
      setTotal(data.total || 0);
      setLanguages(data.languages || []);
      setPage(p);
    } catch {}
    setLoading(false);
  };

  const loadCategories = async () => { try { const r = await fetch('/api/github/categories'); const d = await r.json(); setCategories(d.items || []); } catch {} };
  const [topTags, setTopTags] = useState<TagStat[]>([]);
  const loadTopTags = async () => { try { const r = await fetch('/api/github/tags?limit=20'); const d = await r.json(); setTopTags(d.items || []); } catch {} };
  const loadUnread = async () => { try { const r = await fetch('/api/github/releases/unread-count'); const d = await r.json(); setUnreadCount(d.count || 0); } catch {} };
  const loadReleases = async () => { try { const r = await fetch('/api/github/releases?limit=100'); const d = await r.json(); setReleases(d.items || []); } catch {} };
  const fetchNewReleases = async () => { try { await fetch('/api/github/subscriptions/fetch', { method: 'POST' }); loadReleases(); loadUnread(); } catch {} };
  const loadSubscriptions = async () => { try { const r = await fetch('/api/github/subscriptions'); const d = await r.json(); setSubscriptions(d.items || []); } catch {} };

  const loadReadme = async (itemId: string) => {
    setReadmeLoading(true); setReadmeContent('');
    try { const r = await fetch(`/api/github/stars/${itemId}/readme`); const d = await r.json(); setReadmeContent(d.readme || ''); }
    catch { setReadmeContent('加载失败'); }
    setReadmeLoading(false);
  };

  const loadRemoteReleases = async (fullName: string) => {
    setRemoteReleasesLoading(true); setRemoteReleases([]);
    try { const r = await fetch(`/api/github/releases/fetch?full_name=${encodeURIComponent(fullName)}&per_page=10`); const d = await r.json(); setRemoteReleases(d.items || []); }
    catch { setRemoteReleases([]); }
    setRemoteReleasesLoading(false);
  };

  const loadDiscoverReadme = async (fullName: string) => {
    setDiscoverReadmeLoading(true); setDiscoverReadme('');
    try { const r = await fetch(`/api/github/discover/readme?full_name=${encodeURIComponent(fullName)}`); const d = await r.json(); setDiscoverReadme(d.readme || ''); }
    catch { setDiscoverReadme(''); }
    setDiscoverReadmeLoading(false);
  };

  const loadTrending = async () => {
    // Skip auto-trigger for topic/search if no input
    if (discoverChannel === 'topic' && !topicInput.trim()) { setTrendingItems([]); return; }
    if (discoverChannel === 'search' && !searchInput.trim()) { setTrendingItems([]); return; }

    setLoading(true);
    try {
      let url = '/api/github/discover/trending?since=' + discoverSince;
      if (discoverLang) url += '&language=' + encodeURIComponent(discoverLang);
      if (discoverChannel === 'hot') {
        url = '/api/github/discover/hot';
        if (discoverLang) url += '?language=' + encodeURIComponent(discoverLang);
      }
      else if (discoverChannel === 'popular') {
        url = '/api/github/discover/popular';
        if (discoverLang) url += '?language=' + encodeURIComponent(discoverLang);
      }
      else if (discoverChannel === 'topic') url = '/api/github/discover/topic/' + encodeURIComponent(topicInput.trim());
      else if (discoverChannel === 'search') url = '/api/github/discover/search?q=' + encodeURIComponent(searchInput.trim());

      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const raw = d.items || [];
      setTrendingItems(raw.map((r: Record<string, unknown>) => ({
        full_name: (r.full_name as string) || '', description: (r.description as string) || '',
        language: (r.language as string) || '', stargazers_count: (r.stargazers_count as number) || (r.stars as number) || 0,
        html_url: (r.html_url as string) || '', forks_count: (r.forks_count as number) || (r.forks as number) || 0,
      })));
    } catch (e) {
      console.error('[Discovery] Load error:', e);
      setTrendingItems([]);
      showToast('加载失败，请检查 Token 配置', 'error');
    }
    setLoading(false);
  };

  const sendGhChat = async (question?: string) => {
    const q = (question || ghChatInput).trim();
    if (!q || ghChatLoading) return;
    setGhChatInput('');
    setGhChatLoading(true);
    ghChatAutoScrollRef.current = true;
    const newMsgs = [...ghChatMsgs, { role: 'user', content: q }];
    setGhChatMsgs([...newMsgs, { role: 'ai', content: '' }]);

    const isRepo = !!ghChatRepo;
    const hasRelease = isRepo && ghChatRepo._releaseContext;
    let context = '';
    let mode = 'trends';
    if (hasRelease) {
      context = `仓库: ${ghChatRepo.full_name}\n描述: ${ghChatRepo.description || ''}\n语言: ${ghChatRepo.language || ''}\nStars: ${ghChatRepo.stargazers_count || ghChatRepo.stars || 0}\n\nRelease 记录:\n${ghChatRepo._releaseContext}`;
      mode = 'release';
    } else if (isRepo) {
      context = `仓库: ${ghChatRepo.full_name}\n描述: ${ghChatRepo.description || ''}\n语言: ${ghChatRepo.language || ''}\nStars: ${ghChatRepo.stargazers_count || ghChatRepo.stars || 0}`;
      mode = 'repo';
    } else {
      context = `当前趋势列表:\n${trendingItems.slice(0, 15).map((r, i) => `${i+1}. ${r.full_name} (${r.language || '?'} ⭐${r.stargazers_count}) — ${r.description || ''}`).join('\n')}`;
    }

    try {
      const res = await fetch('/api/github/discover/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, context, mode }),
      });
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let answer = '';
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        for (const line of text.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          try {
            const parsed = JSON.parse(data);
            if (parsed.content) {
              answer += parsed.content;
              setGhChatMsgs(prev => { const c = [...prev]; c[c.length - 1] = { role: 'ai', content: answer }; return c; });
            }
          } catch {}
        }
      }
    } catch {
      setGhChatMsgs(prev => { const c = [...prev]; c[c.length - 1] = { role: 'ai', content: '哎呀，请求出了点问题，检查一下网络或配置吧' }; return c; });
    }
    setGhChatLoading(false);
  };

  const openGhChatForRepo = (repo: GitHubRepo | { full_name: string; description?: string; language?: string; stargazers_count?: number; stars?: number }) => {
    setGhChatRepo(repo);
    setGhChatMsgs([{ role: 'ai', content: `已选择 **${repo.full_name}**，你可以问我关于这个项目的任何问题，比如：\n- 这个项目是做什么的？\n- 技术架构怎么样？\n- 适合什么场景？\n- 怎么快速上手？` }]);
    setGhChatOpen(true);
  };

  const openGhChatForRelease = (repo: { full_name: string }, releases: GitHubRelease[]) => {
    const releaseInfo = releases.map(r => `- ${r.tag_name} (${r.published_at ? new Date(r.published_at).toLocaleDateString() : ''}) ${r.is_prerelease ? '[Pre]' : ''}\n  ${r.body ? r.body.slice(0, 200) : '(无说明)'}`).join('\n');
    setGhChatRepo({ ...repo, _releaseContext: releaseInfo });
    setGhChatMsgs([{ role: 'ai', content: `已加载 **${repo.full_name || repo.full_name}** 的 ${releases.length} 个 Release，你可以问我：\n- 最近有什么重要更新？\n- 总结一下版本变更\n- 有哪些 Breaking Changes？\n- 推荐升级吗？` }]);
    setGhChatOpen(true);
  };

  const openGhChatForTrends = () => {
    setGhChatRepo(null);
    setGhChatMsgs([{ role: 'ai', content: '当前趋势数据已加载，你可以问我：\n- 当前技术热点是什么？\n- 哪些项目值得关注？\n- 分析一下趋势方向\n- 推荐适合初学者的项目' }]);
    setGhChatOpen(true);
  };

  // Highlight code blocks + auto-scroll in GitHub chat messages
  useEffect(() => {
    if (!ghChatOpen) return;
    const timer = setTimeout(() => {
      document.querySelectorAll('.gh-chat-panel .markdown-body pre code').forEach(el => {
        hljs.highlightElement(el as HTMLElement);
      });
      // Auto-scroll only if user is near the bottom (within 150px)
      const el = ghChatMsgsRef.current;
      if (el) {
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
        if (distFromBottom < 150) {
          el.scrollTop = el.scrollHeight;
          ghChatAutoScrollRef.current = true;
        } else {
          ghChatAutoScrollRef.current = false;
        }
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [ghChatMsgs, ghChatOpen]);

  const doSync = async (mode = 'incremental') => {
    setSyncing(true);
    try {
      const fd = new FormData(); fd.append('mode', mode);
      const r = await fetch('/api/github/sync', { method: 'POST', body: fd });
      const d = await r.json();
      if (d.ok) { showToast(`⭐ 同步完成! 新增 ${d.new || 0}, 更新 ${d.updated || 0}`, 'success'); loadStars(); }
      else showToast(d.msg || '同步失败', 'error');
    } catch { showToast('同步失败', 'error'); }
    setSyncing(false);
  };

  const hasToken = ghAccounts.length > 0;

  const saveToken = async () => {
    if (!tokenInput.trim()) return;
    const r = await fetch('/api/github/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'add', token: tokenInput.trim() }) });
    const d = await r.json();
    if (d.ok) { showToast('账号已添加，开始同步...', 'success'); setTokenInput(''); setShowConfig(false); checkConfig(); doSync('full'); }
    else showToast(d.msg || '添加失败', 'error');
  };

  useEffect(() => { if (hasToken) loadStars(1); }, [sort, language, category]);
  useEffect(() => {
    if (currentView === 'repositories' && hasToken) loadStars(1);
    else if (currentView === 'releases') { loadReleases(); loadSubscriptions(); fetchNewReleases(); }
    else if (currentView === 'discover') loadTrending();
    else if (currentView === 'settings') loadCategories();
  }, [currentView]);

  useEffect(() => {
    if (currentView !== 'discover') return;
    // Don't auto-trigger for topic/search — require user action
    if (discoverChannel === 'topic' || discoverChannel === 'search') return;
    loadTrending();
  }, [discoverChannel, discoverSince, discoverLang]);
  useEffect(() => { if (detailTab === 'readme' && detailRepo && !readmeContent && !readmeLoading) loadReadme(detailRepo.item_id); }, [detailTab]);
  useEffect(() => { if (detailTab === 'releases' && detailRepo && releases.filter(r => r.item_id === detailRepo.item_id).length === 0 && !remoteReleasesLoading) loadRemoteReleases(detailRepo.full_name); }, [detailTab]);

  const formatNum = (n: number) => n >= 1000000 ? (n/1000000).toFixed(1)+'M' : n >= 1000 ? (n/1000).toFixed(1)+'k' : String(n);
  const timeAgo = (s: string) => { if (!s) return ''; const d = Date.now() - new Date(s).getTime(); const m = Math.floor(d/60000); if (m < 60) return m+'分钟前'; const h = Math.floor(m/60); if (h < 24) return h+'小时前'; const dy = Math.floor(h/24); if (dy < 30) return dy+'天前'; return Math.floor(dy/30)+'个月前'; };

  // ── Login Screen ──
  if (!hasToken) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', gap: '24px' }}>
        <div style={{ width: 80, height: 80, borderRadius: '20px', background: 'linear-gradient(135deg, #8b5cf6, #3b82f6)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Star size={40} color="#fff" />
        </div>
        <h1 style={{ fontSize: '1.8rem', fontWeight: 700 }}>GitHub Stars Manager</h1>
        <p style={{ color: 'var(--text2, #888)', maxWidth: 400, textAlign: 'center' }}>同步你的 GitHub 星标仓库，AI 智能分析摘要和分类，追踪 Release 更新，发现热门趋势</p>
        <div style={{ width: '100%', maxWidth: 420 }}>
          <div style={{ background: 'var(--surface)', borderRadius: '16px', padding: '24px', border: '1px solid var(--border, #333)' }}>
            {showConfig ? (
              <>
                <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '8px' }}>添加 GitHub Token</label>
                <input type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)} placeholder="ghp_..."
                  onKeyDown={e => { if (e.key === 'Enter') saveToken(); }}
                  style={{ width: '100%', padding: '12px 16px', borderRadius: '10px', border: '1px solid var(--border, #333)', background: 'var(--surface2, #2a2a3e)', color: 'var(--text, #fff)', fontSize: '0.95rem', marginBottom: '12px', boxSizing: 'border-box' }} />
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button onClick={saveToken} style={{ flex: 1, padding: '12px', borderRadius: '10px', border: 'none', background: 'linear-gradient(135deg, #8b5cf6, #3b82f6)', color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: '0.95rem' }}>添加并同步</button>
                  <button onClick={() => setShowConfig(false)} style={{ padding: '12px 20px', borderRadius: '10px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer' }}>取消</button>
                </div>
                <p style={{ fontSize: '0.75rem', color: 'var(--text2, #666)', marginTop: '12px', lineHeight: 1.5 }}>
                  支持多个账号，每个账号独立同步。<br/>
                  需要 <code style={{ background: 'var(--surface2)', padding: '1px 4px', borderRadius: '3px' }}>read:user</code> scope —
                  在 <a href="https://github.com/settings/tokens" target="_blank" rel="noopener" style={{ color: 'var(--accent, #8b5cf6)' }}>github.com/settings/tokens</a> 创建
                </p>
              </>
            ) : (
              <button onClick={() => setShowConfig(true)} style={{ width: '100%', padding: '14px', borderRadius: '12px', border: 'none', background: 'linear-gradient(135deg, #8b5cf6, #3b82f6)', color: '#fff', fontWeight: 600, cursor: 'pointer', fontSize: '1.05rem', boxShadow: '0 4px 15px rgba(139,92,246,0.3)' }}>添加 GitHub 账号</button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── Nav Tabs ──
  const navItems = [
    { id: 'repositories' as const, icon: <Star size={16}/>, label: '星标仓库', count: total },
    { id: 'releases' as const, icon: <Bell size={16}/>, label: 'Release', badge: unreadCount },
    { id: 'discover' as const, icon: <TrendingUp size={16}/>, label: '发现' },
    { id: 'settings' as const, icon: <Tag size={16}/>, label: '分类管理' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Top Nav Bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '8px 12px', borderBottom: '1px solid var(--border, #333)', flexShrink: 0 }}>
        {navItems.map(n => (
          <button key={n.id} onClick={() => setCurrentView(n.id)}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px', borderRadius: '8px', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', position: 'relative',
              background: currentView === n.id ? 'var(--accent, #8b5cf6)' : 'transparent',
              color: currentView === n.id ? '#fff' : 'var(--text2, #888)' }}>
            {n.icon}{n.label}
            {n.count !== undefined && currentView === n.id && <span style={{ fontSize: '0.7rem', opacity: 0.8 }}>{n.count}</span>}
            {n.badge ? <span style={{ fontSize: '0.65rem', padding: '1px 5px', borderRadius: '8px', background: 'var(--accent)', color: '#fff', marginLeft: '-4px' }}>{n.badge}</span> : null}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        {ghAccounts.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginRight: '8px', position: 'relative' }}>
            {ghAccounts.filter(a => a.enabled !== false).map((acc, i) => (
              <div key={acc.login || i} title={acc.login || acc.label || `账号 ${i + 1}`} style={{ marginLeft: i > 0 ? '-8px' : '0', position: 'relative', zIndex: ghAccounts.length - i }}>
                <img src={acc.avatar_url || 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="50" fill="%23666"/></svg>'} alt="" style={{ width: 28, height: 28, borderRadius: '50%', border: '2px solid var(--surface, #1a1a2e)' }} />
              </div>
            ))}
            <button onClick={() => setShowConfig(!showConfig)} title="管理账号" style={{ width: 28, height: 28, borderRadius: '50%', border: '1px dashed var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 700, lineHeight: 1, marginLeft: '4px' }}>+</button>
            {showConfig && (
              <div style={{ position: 'absolute', top: '100%', right: 0, marginTop: '8px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '12px', padding: '16px', width: '340px', zIndex: 100, boxShadow: '0 8px 30px rgba(0,0,0,0.3)' }}>
                <div style={{ fontWeight: 700, fontSize: '0.9rem', marginBottom: '12px' }}>账号管理</div>
                {ghAccounts.map((acc, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                    <img src={acc.avatar_url || ''} alt="" style={{ width: 24, height: 24, borderRadius: '50%', opacity: acc.enabled === false ? 0.4 : 1 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: '0.8rem', fontWeight: 600, opacity: acc.enabled === false ? 0.5 : 1 }}>{acc.login || acc.label}</div>
                      <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>{acc.token_preview}</div>
                    </div>
                    <button onClick={async () => { await fetch('/api/github/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'toggle', login: acc.login, enabled: acc.enabled === false }) }); checkConfig(); }}
                      style={{ padding: '2px 8px', borderRadius: '4px', border: '1px solid var(--border)', background: 'transparent', color: acc.enabled === false ? 'var(--accent)' : 'var(--text-muted)', cursor: 'pointer', fontSize: '0.7rem' }}>
                      {acc.enabled === false ? '启用' : '禁用'}
                    </button>
                    <button onClick={async () => { if (confirmDel !== acc.login) { setConfirmDel(acc.login); return; } await fetch('/api/github/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'remove', login: acc.login }) }); setConfirmDel(''); checkConfig(); }}
                      style={{ padding: '2px 6px', borderRadius: '4px', border: 'none', background: 'transparent', color: 'var(--red, #ef4444)', cursor: 'pointer', fontSize: '0.7rem' }}>
                      ✕
                    </button>
                  </div>
                ))}
                <div style={{ marginTop: '12px' }}>
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <input type="password" value={tokenInput} onChange={e => setTokenInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') saveToken(); }}
                      placeholder="ghp_新增 Token..."
                      style={{ flex: 1, padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', fontSize: '0.8rem' }} />
                    <button onClick={saveToken} style={{ padding: '8px 14px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}>添加</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
        <button onClick={() => doSync('incremental')} disabled={syncing}
          style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px', borderRadius: '8px', border: 'none', cursor: syncing ? 'wait' : 'pointer', fontSize: '0.85rem', fontWeight: 600,
            background: syncing ? 'var(--surface2)' : 'var(--accent, #8b5cf6)', color: '#fff' }}>
          <RefreshCw size={14} style={syncing ? { animation: 'spin 1s linear infinite' } : {}} />{syncing ? '同步中...' : '同步'}
        </button>
        <button onClick={async () => {
          showToast('🤖 开始 AI 分析所有仓库...');
          try { const r = await fetch('/api/github/stars/reanalyze-all', { method: 'POST' }); const d = await r.json(); showToast(`AI 分析完成: ${d.analyzed || 0}/${d.total || 0}`, 'success'); loadStars(1); } catch { showToast('分析失败', 'error'); }
        }} style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.8rem' }}>🤖 AI</button>
      </div>

      {/* Content Area */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px' }}>

        {/* ═══ REPOSITORIES VIEW ═══ */}
        {currentView === 'repositories' && (
          <div style={{ display: 'flex', gap: '16px' }}>
            {/* Category Sidebar */}
            <div style={{ width: 200, flexShrink: 0 }}>
              <div style={{ background: 'var(--surface)', borderRadius: '12px', border: '1px solid var(--border, #333)', overflow: 'hidden' }}>
                <div style={{ padding: '12px 16px', fontWeight: 700, fontSize: '0.85rem', borderBottom: '1px solid var(--border, #333)' }}>分类</div>
                <div onClick={() => setCategory('')} style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between',
                  background: !category ? 'var(--accent-dim, #8b5cf615)' : 'transparent', color: !category ? 'var(--accent, #8b5cf6)' : 'var(--text2)' }}>
                  <span>全部</span><span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{total}</span>
                </div>
                {categories.map(c => (
                  <div key={c.id} onClick={() => setCategory(c.id)} style={{ padding: '10px 16px', cursor: 'pointer', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    background: category === c.id ? 'var(--accent-dim, #8b5cf615)' : 'transparent', color: category === c.id ? 'var(--accent, #8b5cf6)' : 'var(--text2)' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: c.color, flexShrink: 0 }} />
                      {c.icon} {c.name}
                    </span>
                    <span style={{ fontSize: '0.75rem', opacity: 0.6 }}>{c.repo_count || 0}</span>
                  </div>
                ))}
              </div>
              {/* Top Tags */}
              {topTags.length > 0 && (
                <div style={{ background: 'var(--surface)', borderRadius: '12px', border: '1px solid var(--border, #333)', overflow: 'hidden', marginTop: '12px' }}>
                  <div style={{ padding: '12px 16px', fontWeight: 700, fontSize: '0.85rem', borderBottom: '1px solid var(--border, #333)' }}>热门标签</div>
                  <div style={{ padding: '10px 12px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {topTags.map((t: TagStat) => (
                      <span key={t.name} onClick={() => { setSearch(t.name); loadStars(1, t.name); }}
                        style={{ fontSize: '0.72rem', padding: '3px 8px', borderRadius: '6px', cursor: 'pointer', fontWeight: search === t.name ? 700 : 400,
                          background: search === t.name ? 'var(--accent, #8b5cf6)25' : 'var(--surface2)', color: search === t.name ? 'var(--accent, #8b5cf6)' : 'var(--text2)',
                          border: search === t.name ? '1px solid var(--accent, #8b5cf6)40' : '1px solid transparent' }}>
                        {t.name} <span style={{ opacity: 0.5, fontSize: '0.65rem' }}>{t.count}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Main Content */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {/* Search & Filters */}
              <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 220, position: 'relative' }}>
                  <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text2)' }} />
                  <input value={search} onChange={e => { const v = e.target.value; setSearch(v); if (!v) loadStars(1, ''); }} onKeyDown={e => e.key === 'Enter' && loadStars(1)}
                    placeholder="搜索仓库名称、描述、标签..."
                    style={{ width: '100%', padding: '10px 12px 10px 36px', borderRadius: '10px', border: '1px solid var(--border, #333)', background: 'var(--surface)', color: 'var(--text, #fff)', fontSize: '0.9rem', boxSizing: 'border-box' }} />
                </div>
                <select value={language} onChange={e => setLanguage(e.target.value)}
                  style={{ padding: '10px 12px', borderRadius: '10px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <option value="">所有语言</option>
                  {languages.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
                <select value={sort} onChange={e => setSort(e.target.value)}
                  style={{ padding: '10px 12px', borderRadius: '10px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <option value="stars">⭐ 最多 Star</option>
                  <option value="recently">🕐 最近添加</option>
                  <option value="name">🔤 名称</option>
                </select>
              </div>

              {/* Repository Cards */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {items.map(repo => {
                  let tags: string[] = [];
                  try { tags = JSON.parse(repo.ai_tags || '[]'); } catch {}
                  const langColor = LANG_COLORS[repo.language] || '#8b5cf6';
                  return (
                    <div key={repo.item_id} onClick={() => { setDetailRepo(repo); setDetailTab('info'); setReadmeContent(''); }}
                      style={{ background: 'var(--surface)', borderRadius: '12px', padding: '16px 20px', border: '1px solid var(--border, #333)', cursor: 'pointer', transition: 'all 0.15s' }}>
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px' }}>
                        {/* Owner Avatar */}
                        <img src={`https://github.com/${repo.owner}.png?size=48`} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                          style={{ width: 44, height: 44, borderRadius: '10px', flexShrink: 0, background: 'var(--surface2)' }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          {/* Name row */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                            <span style={{ fontWeight: 700, fontSize: '1rem', color: 'var(--text, #fff)' }}>{repo.full_name}</span>
                            {subscribedIds.has(repo.item_id) && <Bell size={14} color="#3b82f6" />}
                            {repo.category_name && <span style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: '10px', background: (repo.category_color || '#8b5cf6') + '20', color: repo.category_color || '#8b5cf6' }}>{repo.category_icon} {repo.category_name}</span>}
                          </div>
                          {/* Description */}
                          <p style={{ fontSize: '0.85rem', color: 'var(--text2, #888)', margin: '0 0 8px', lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                            {repo.ai_summary || repo.description || ''}
                          </p>
                          {/* Meta row */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap' }}>
                            {repo.language && (
                              <span style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.8rem', color: 'var(--text2)' }}>
                                <span style={{ width: 10, height: 10, borderRadius: '50%', background: langColor }} />
                                {repo.language}
                              </span>
                            )}
                            <span style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '0.8rem', color: '#f59e0b' }}>
                              <Star size={12} /> {formatNum(repo.stars || 0)}
                            </span>
                            <span style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '0.8rem', color: 'var(--text2)' }}>
                              <GitBranch size={12} /> {formatNum(repo.forks || 0)}
                            </span>
                            {tags.length > 0 && (
                              <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                                {tags.slice(0, 3).map((t: string) => <span key={t} onClick={(e) => { e.stopPropagation(); setSearch(t); loadStars(1, t); }} style={{ fontSize: '0.7rem', padding: '2px 7px', borderRadius: '6px', background: 'var(--accent, #8b5cf6)15', color: 'var(--accent, #8b5cf6)', cursor: 'pointer' }} title={`筛选: ${t}`}>{t}</span>)}
                              </div>
                            )}
                          </div>
                        </div>
                        <button onClick={e => { e.stopPropagation(); openGhChatForRepo(repo); }}
                          style={{ padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--accent)', cursor: 'pointer', fontSize: '0.78rem', fontWeight: 500, whiteSpace: 'nowrap', alignSelf: 'center' }}>🤖 AI 问答</button>
                      </div>
                    </div>
                  );
                })}
              </div>

              {items.length < total && (
                <div style={{ textAlign: 'center', marginTop: '20px' }}>
                  <button onClick={() => loadStars(page + 1)}
                    style={{ padding: '10px 28px', borderRadius: '10px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.85rem' }}>加载更多 ({items.length}/{total})</button>
                </div>
              )}
              {items.length === 0 && !loading && <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text2)' }}>还没有星标仓库，点右上角同步一下吧～</div>}
            </div>
          </div>
        )}

        {/* ═══ RELEASES VIEW ═══ */}
        {currentView === 'releases' && (
          <div style={{ maxWidth: 900, margin: '0 auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
              <h2 style={{ margin: 0, fontSize: '1.2rem' }}>Release 时间线</h2>
              <div style={{ display: 'flex', gap: '4px', marginLeft: 'auto' }}>
                {['mac','win','linux','arm','deb','rpm','apk'].map(pf => (
                  <button key={pf} onClick={() => setReleaseFilter(releaseFilter === pf ? '' : pf)}
                    style={{ fontSize: '0.7rem', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--border)', cursor: 'pointer',
                      background: releaseFilter === pf ? 'var(--accent)' : 'transparent', color: releaseFilter === pf ? '#fff' : 'var(--text2)' }}>{pf}</button>
                ))}
              </div>
              <button onClick={() => { loadReleases(); loadSubscriptions(); }} style={{ padding: '6px 12px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.8rem' }}>刷新</button>
              <button onClick={() => {
                if (releases.length === 0) { showToast('暂无 Release', 'info'); return; }
                // Group releases by repo
                const byRepo: Record<string, GitHubRelease[]> = {};
                releases.forEach(r => { (byRepo[r.full_name] = byRepo[r.full_name] || []).push(r); });
                // Open chat for the first repo with most releases
                const top = Object.entries(byRepo).sort((a, b) => b[1].length - a[1].length)[0];
                if (top) openGhChatForRelease({ full_name: top[0] }, top[1]);
              }} style={{ padding: '6px 12px', borderRadius: '6px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 600 }}>🤖 AI 分析</button>
            </div>
            {subscriptions.length > 0 && (
              <div style={{ marginBottom: '16px', padding: '12px 16px', background: 'var(--surface)', borderRadius: '10px', border: '1px solid var(--border)' }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text2)', marginBottom: '8px' }}>已订阅 ({subscriptions.length})</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                  {subscriptions.map((s: Subscription) => (
                    <span key={s.item_id} style={{ fontSize: '0.8rem', padding: '4px 10px', borderRadius: '8px', background: 'var(--surface2)', color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <Bell size={12} color="#3b82f6" />{s.full_name}
                      <button onClick={async () => { await fetch(`/api/github/subscriptions/${s.item_id}`, { method: 'DELETE' }); loadSubscriptions(); showToast('已取消订阅'); }}
                        style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', padding: 0, fontSize: '0.8rem', lineHeight: 1 }}>×</button>
                    </span>
                  ))}
                </div>
              </div>
            )}
            {(releaseFilter ? releases.filter(r => { try { const a = typeof r.assets === 'string' ? JSON.parse(r.assets || '[]') : (r.assets || []); return a.some((x: ReleaseAsset) => x.name?.toLowerCase().includes(releaseFilter)); } catch { return false; } }) : releases).map(rel => {
              const assets: ReleaseAsset[] = (() => { try { return typeof rel.assets === 'string' ? JSON.parse(rel.assets || '[]') : (rel.assets || []); } catch { return []; } })();
              const filteredAssets = releaseFilter ? assets.filter((a: ReleaseAsset) => a.name?.toLowerCase().includes(releaseFilter)) : assets;
              return (
                <div key={rel.id} style={{ background: 'var(--surface)', borderRadius: '12px', padding: '16px 20px', border: '1px solid var(--border)', marginBottom: '10px', opacity: rel.is_read ? 0.5 : 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
                    {!rel.is_read && <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#3b82f6', flexShrink: 0 }} />}
                    <a href={rel.html_url} target="_blank" rel="noopener" style={{ fontWeight: 700, color: 'var(--text)', textDecoration: 'none', fontSize: '0.95rem' }}>{rel.full_name}</a>
                    <span style={{ fontSize: '0.85rem', color: 'var(--accent, #8b5cf6)', fontWeight: 600 }}>{rel.tag_name}</span>
                    {rel.is_prerelease ? <span style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: '6px', background: '#f59e0b20', color: '#f59e0b', fontWeight: 600 }}>Pre</span> : null}
                    <span style={{ fontSize: '0.8rem', color: 'var(--text2)', marginLeft: 'auto' }}>{timeAgo(rel.published_at)}</span>
                  </div>
                  {rel.name && rel.name !== rel.tag_name && <p style={{ fontSize: '0.85rem', color: 'var(--text2)', margin: '4px 0', fontWeight: 600 }}>{rel.name}</p>}
                  {rel.body && <p style={{ fontSize: '0.8rem', color: 'var(--text2)', margin: '6px 0 0', maxHeight: '80px', overflow: 'hidden', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{rel.body.slice(0, 400)}</p>}
                  {filteredAssets.length > 0 && (
                    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text2)', fontWeight: 600 }}>下载资产 ({filteredAssets.length})</div>
                      {filteredAssets.slice(0, 5).map((a: ReleaseAsset, i: number) => (
                        <a key={i} href={a.url} target="_blank" rel="noopener" style={{ fontSize: '0.8rem', color: 'var(--accent)', textDecoration: 'none', display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <Download size={12} />{a.name}{a.size ? <span style={{ color: 'var(--text2)', fontSize: '0.7rem' }}>({(a.size/1048576).toFixed(1)} MB)</span> : null}
                        </a>
                      ))}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: '8px', marginTop: '10px', alignItems: 'center' }}>
                    {!rel.is_read && <button onClick={async () => { await fetch(`/api/github/releases/${rel.id}/read`, { method: 'POST' }); loadReleases(); loadUnread(); }}
                      style={{ fontSize: '0.75rem', padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer' }}>标记已读</button>}
                    <a href={rel.html_url} target="_blank" rel="noopener" style={{ fontSize: '0.75rem', padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--accent)', textDecoration: 'none' }}>查看 Release</a>
                    <button onClick={() => openGhChatForRelease({ full_name: rel.full_name }, [rel])}
                      style={{ fontSize: '0.75rem', padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--accent)', cursor: 'pointer', marginLeft: 'auto' }}>🤖 AI 分析</button>
                  </div>
                </div>
              );
            })}
            {releases.length === 0 && <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text2)' }}>还没有 Release 记录，订阅仓库后会自动追踪哦～</div>}
          </div>
        )}

        {/* ═══ DISCOVERY VIEW ═══ */}
        {currentView === 'discover' && (
          <div>
            <div style={{ display: 'flex', gap: '6px', marginBottom: '16px', flexWrap: 'wrap' }}>
              {(['trending','hot','popular','topic','search'] as const).map(ch => (
                <button key={ch} onClick={() => setDiscoverChannel(ch)}
                  style={{ padding: '10px 18px', borderRadius: '10px', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem',
                    background: discoverChannel === ch ? 'var(--accent, #8b5cf6)' : 'var(--surface)',
                    color: discoverChannel === ch ? '#fff' : 'var(--text2)', boxShadow: discoverChannel === ch ? '0 2px 8px rgba(139,92,246,0.3)' : 'none' }}>
                  {{trending:'趋势',hot:'热门发布',popular:'最多 Star',topic:'Topic',search:'搜索'}[ch]}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '8px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
              {discoverChannel === 'trending' && (<>
                <select value={discoverSince} onChange={e => setDiscoverSince(e.target.value)} style={{ padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <option value="daily">今日</option><option value="weekly">本周</option><option value="monthly">本月</option>
                </select>
                <select value={discoverLang} onChange={e => setDiscoverLang(e.target.value)} style={{ padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <option value="">所有语言</option>{['JavaScript','TypeScript','Python','Go','Rust','Java','C++','Ruby','PHP','Swift','Kotlin'].map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </>)}
              {discoverChannel === 'popular' && (
                <select value={discoverLang} onChange={e => setDiscoverLang(e.target.value)} style={{ padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <option value="">所有语言</option>{languages.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              )}
              {discoverChannel === 'topic' && (<>
                <input value={topicInput} onChange={e => setTopicInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadTrending()} placeholder="输入 topic (如: machine-learning)" style={{ flex: 1, padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }} />
                <button onClick={loadTrending} style={{ padding: '8px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600 }}>搜索</button>
              </>)}
              {discoverChannel === 'search' && (<>
                <input value={searchInput} onChange={e => setSearchInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && loadTrending()} placeholder="搜索 GitHub 仓库..." style={{ flex: 1, padding: '8px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text)', fontSize: '0.85rem' }} />
                <button onClick={loadTrending} style={{ padding: '8px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600 }}>搜索</button>
              </>)}
              <button onClick={loadTrending} style={{ padding: '8px 16px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.85rem' }}>刷新</button>
              <button onClick={openGhChatForTrends} style={{ padding: '8px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600 }}>🤖 AI 分析趋势</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: '12px' }}>
              {trendingItems.map((repo: TrendingItem, i: number) => (
                <div key={repo.full_name || i} onClick={() => { setDiscoverDetail(repo); setDiscoverReadme(''); loadDiscoverReadme(repo.full_name); }}
                  style={{ background: 'var(--surface)', borderRadius: '12px', padding: '16px', border: '1px solid var(--border)', cursor: 'pointer' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                    <img src={`https://github.com/${(repo.full_name||'').split('/')[0]}.png?size=32`} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                      style={{ width: 36, height: 36, borderRadius: '8px', background: 'var(--surface2)' }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text)' }}>{repo.full_name}</span>
                      <div style={{ display: 'flex', gap: '10px', marginTop: '3px' }}>
                        {repo.language && <span style={{ fontSize: '0.75rem', color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: '3px' }}>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', background: LANG_COLORS[repo.language] || '#8b5cf6' }} />{repo.language}
                        </span>}
                        {repo.stargazers_count > 0 && <span style={{ fontSize: '0.75rem', color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '2px' }}><Star size={10} />{formatNum(repo.stargazers_count)}</span>}
                      </div>
                    </div>
                  </div>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text2)', margin: 0, lineHeight: 1.4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{repo.description || ''}</p>
                  <div style={{ display: 'flex', gap: '6px', marginTop: '8px', alignItems: 'center' }}>
                    <button onClick={e => { e.stopPropagation(); openGhChatForRepo(repo); }}
                      style={{ padding: '4px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--accent)', cursor: 'pointer', fontSize: '0.78rem', fontWeight: 500 }}>🤖 AI 问答</button>
                    <StarButton fullName={repo.full_name} accounts={ghAccounts} starRepo={starRepo} compact />
                  </div>
                </div>
              ))}
            </div>
            {trendingItems.length === 0 && !loading && <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text2)' }}>{discoverChannel === 'topic' ? '输入 Topic 后搜索' : discoverChannel === 'search' ? '输入关键词搜索' : '暂无数据'}</div>}
          </div>
        )}

        {/* ═══ CATEGORIES / SETTINGS VIEW ═══ */}
        {currentView === 'settings' && (
          <div style={{ maxWidth: 700, margin: '0 auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '20px' }}>
              <h2 style={{ margin: 0, fontSize: '1.2rem' }}>分类管理</h2>
              <button onClick={() => { setCatName(''); setCatKeywords(''); setCatColor('#8b5cf6'); setCatIcon('📁'); setEditingCat(null); setShowCatModal(true); }}
                style={{ padding: '8px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent, #8b5cf6)', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>+ 新建分类</button>
              <button onClick={async () => {
                const r = await fetch('/api/github/categories/auto-assign', { method: 'POST' }); const d = await r.json();
                showToast(`AI 自动分类完成，分配了 ${d.assigned || 0} 个仓库`, 'success'); loadCategories(); loadStars(1); loadTopTags();
              }} style={{ padding: '8px 16px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.85rem' }}>🤖 AI 自动分类</button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {categories.map(cat => (
                <div key={cat.id} style={{ background: 'var(--surface)', borderRadius: '12px', padding: '14px 18px', border: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ width: 14, height: 14, borderRadius: '50%', background: cat.color, flexShrink: 0 }} />
                  <span style={{ fontSize: '1.1rem' }}>{cat.icon}</span>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>{cat.name}</span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text2)', marginLeft: '8px' }}>{cat.repo_count || 0} 个仓库</span>
                  </div>
                  <button onClick={() => { setEditingCat(cat); setCatName(cat.name); setCatKeywords(cat.keywords || '[]'); setCatColor(cat.color); setCatIcon(cat.icon); setShowCatModal(true); }}
                    style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.8rem' }}>编辑</button>
                  <button onClick={async () => { await fetch(`/api/github/categories/${cat.id}`, { method: 'DELETE' }); loadCategories(); }}
                    style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #ef444440', background: 'transparent', color: '#ef4444', cursor: 'pointer', fontSize: '0.8rem' }}>删除</button>
                </div>
              ))}
              {categories.length === 0 && <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text2)' }}>暂无分类，点击新建</div>}
            </div>
          </div>
        )}
      </div>

      {/* ═══ DETAIL MODAL ═══ */}
      {detailRepo && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', zIndex: 1000, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '20px' }} onClick={() => setDetailRepo(null)}>
          <div style={{ background: 'var(--surface)', borderRadius: '16px', width: '100%', maxWidth: detailTab === 'readme' ? 1200 : 800, maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.5)', transition: 'max-width 0.25s ease' }} onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '14px' }}>
              <img src={`https://github.com/${detailRepo.owner}.png?size=64`} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                style={{ width: 52, height: 52, borderRadius: '12px', background: 'var(--surface2)' }} />
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <h2 style={{ margin: 0, fontSize: '1.3rem' }}>{detailRepo.full_name}</h2>
                  {subscribedIds.has(detailRepo.item_id) && <Bell size={16} color="#3b82f6" />}
                </div>
                <p style={{ margin: '4px 0 0', fontSize: '0.9rem', color: 'var(--text2)' }}>{detailRepo.description}</p>
              </div>
              <a href={detailRepo.html_url} target="_blank" rel="noopener" style={{ color: 'var(--accent)', padding: '8px' }}><ExternalLink size={18} /></a>
              <UnstarButton fullName={detailRepo.full_name} onDone={() => { setDetailRepo(null); loadStars(1); }} starRepo={starRepo} />
              <button onClick={() => setDetailRepo(null)} style={{ background: 'none', border: 'none', color: 'var(--text2)', cursor: 'pointer', padding: '8px' }}><X size={20} /></button>
            </div>

            {/* Tabs */}
            <div style={{ display: 'flex', gap: '0', borderBottom: '1px solid var(--border)' }}>
              {[{id:'info' as const,label:'信息'},{id:'releases' as const,label:'Releases'},{id:'readme' as const,label:'README'}].map(t => (
                <button key={t.id} onClick={() => setDetailTab(t.id)}
                  style={{ padding: '12px 20px', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', borderBottom: detailTab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
                    background: 'transparent', color: detailTab === t.id ? 'var(--accent)' : 'var(--text2)' }}>{t.label}</button>
              ))}
            </div>

            {/* Tab Content */}
            <div style={{ flex: 1, overflow: detailTab === 'readme' ? 'hidden' : 'auto', padding: detailTab === 'readme' ? 0 : '20px 24px', display: detailTab === 'readme' ? 'flex' : 'block', flexDirection: detailTab === 'readme' ? 'column' : undefined }}>
              {detailTab === 'info' && (
                <div>
                  {/* Stats */}
                  <div style={{ display: 'flex', gap: '20px', marginBottom: '20px', flexWrap: 'wrap' }}>
                    {detailRepo.language && <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ width: 12, height: 12, borderRadius: '50%', background: LANG_COLORS[detailRepo.language] || '#8b5cf6' }} />
                      <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>{detailRepo.language}</span>
                    </div>}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><Star size={14} color="#f59e0b" /><span style={{ fontSize: '0.9rem', fontWeight: 600 }}>{formatNum(detailRepo.stars || 0)}</span><span style={{ fontSize: '0.8rem', color: 'var(--text2)' }}>stars</span></div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><GitBranch size={14} color="var(--text2)" /><span style={{ fontSize: '0.9rem', fontWeight: 600 }}>{formatNum(detailRepo.forks || 0)}</span><span style={{ fontSize: '0.8rem', color: 'var(--text2)' }}>forks</span></div>
                    {detailRepo.license && <span style={{ fontSize: '0.85rem', color: 'var(--text2)' }}>📜 {detailRepo.license}</span>}
                  </div>

                  {/* AI Summary */}
                  {detailRepo.ai_summary && <div style={{ padding: '14px', borderRadius: '10px', background: 'var(--surface2)', marginBottom: '16px', borderLeft: '3px solid var(--accent, #8b5cf6)' }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--accent)', fontWeight: 700, marginBottom: '4px' }}>AI 摘要</div>
                    <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.5 }}>{detailRepo.ai_summary}</p>
                  </div>}

                  {/* Tags */}
                  {(() => { try { const tags = JSON.parse(detailRepo.ai_tags || '[]'); return tags.length > 0 ? (
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
                      {tags.map((t: string) => <span key={t} style={{ fontSize: '0.8rem', padding: '4px 10px', borderRadius: '8px', background: 'var(--accent, #8b5cf6)15', color: 'var(--accent)', fontWeight: 500 }}>{t}</span>)}
                    </div>
                  ) : null; } catch { return null; } })()}

                  {/* Topics */}
                  {(() => { try { const topics = JSON.parse(detailRepo.topics || '[]'); return topics.length > 0 ? (
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
                      {topics.map((t: string) => <span key={t} style={{ fontSize: '0.75rem', padding: '3px 8px', borderRadius: '6px', background: 'var(--surface2)', color: 'var(--text2)' }}>{t}</span>)}
                    </div>
                  ) : null; } catch { return null; } })()}

                  {/* Actions */}
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button onClick={async () => {
                      const isSub = subscribedIds.has(detailRepo.item_id);
                      if (isSub) { await fetch(`/api/github/subscriptions/${detailRepo.item_id}`, { method: 'DELETE' }); showToast('已取消订阅', 'success'); }
                      else { const fd = new FormData(); fd.append('item_id', detailRepo.item_id); fd.append('full_name', detailRepo.full_name); await fetch('/api/github/subscriptions', { method: 'POST', body: fd }); showToast('已订阅 Release', 'success'); }
                      loadSubscriptions();
                    }} style={{ padding: '10px 18px', borderRadius: '8px', border: 'none', background: subscribedIds.has(detailRepo.item_id) ? '#ef4444' : 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>
                      {subscribedIds.has(detailRepo.item_id) ? '🔕 取消订阅' : '🔔 订阅 Release'}
                    </button>
                    <button onClick={async () => {
                      await fetch(`/api/github/stars/${detailRepo.item_id}/reanalyze`, { method: 'POST' }); showToast('AI 重新分析完成', 'success'); loadStars(1);
                    }} style={{ padding: '10px 18px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.85rem' }}>🤖 重新分析</button>
                    <button onClick={() => openGhChatForRepo(detailRepo)} style={{ padding: '10px 18px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>🤖 AI 问答</button>
                    <a href={`https://deepwiki.com/${detailRepo.full_name}`} target="_blank" rel="noopener"
                      style={{ padding: '10px 18px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', textDecoration: 'none', fontSize: '0.85rem' }}>📖 DeepWiki</a>
                  </div>
                  <div style={{ marginTop: '16px', fontSize: '0.8rem', color: 'var(--text2)' }}>最后同步: {detailRepo.last_synced ? new Date(detailRepo.last_synced).toLocaleString() : '未知'}</div>
                </div>
              )}

              {detailTab === 'releases' && (
                <div>
                  <button onClick={() => {
                    const repoReleases = releases.filter(r => r.item_id === detailRepo.item_id);
                    if (repoReleases.length > 0) openGhChatForRelease(detailRepo, repoReleases);
                    else if (remoteReleases.length > 0) openGhChatForRelease(detailRepo, remoteReleases);
                    else showToast('暂无 Release 数据', 'info');
                  }} style={{ marginBottom: '12px', padding: '8px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>🤖 AI 分析 Release</button>
                  {(() => {
                    const repoReleases = releases.filter(r => r.item_id === detailRepo.item_id);
                    const displayReleases = repoReleases.length > 0 ? repoReleases : remoteReleases;
                    const isLoading = repoReleases.length === 0 && remoteReleasesLoading;
                    if (isLoading) return <div style={{ textAlign: 'center', padding: '30px', color: 'var(--text2)' }}>加载中...</div>;
                    return displayReleases.length > 0 ? displayReleases.map((rel: GitHubRelease & { full_name?: string; is_read?: boolean; owner?: string; last_synced?: string; license?: string; html_url?: string }, idx: number) => (
                      <div key={rel.id || idx} style={{ background: 'var(--surface2)', borderRadius: '10px', padding: '14px', marginBottom: '10px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                          <span style={{ fontWeight: 700, color: 'var(--accent)' }}>{rel.tag_name}</span>
                          {rel.is_prerelease && <span style={{ fontSize: '0.7rem', padding: '2px 6px', borderRadius: '4px', background: '#f59e0b20', color: '#f59e0b' }}>Pre</span>}
                          <span style={{ fontSize: '0.8rem', color: 'var(--text2)', marginLeft: 'auto' }}>{rel.published_at ? new Date(rel.published_at).toLocaleDateString() : ''}</span>
                        </div>
                        {rel.body && <p style={{ fontSize: '0.8rem', color: 'var(--text2)', margin: 0, maxHeight: '100px', overflow: 'auto', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{rel.body.slice(0, 500)}</p>}
                        {/* Assets */}
                        {(() => { try { const assets = typeof rel.assets === 'string' ? JSON.parse(rel.assets) : (rel.assets || []); return assets.length > 0 ? (
                          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }}>
                            {assets.map((a: ReleaseAsset, ai: number) => <a key={ai} href={a.url} target="_blank" rel="noopener" style={{ fontSize: '0.75rem', padding: '3px 8px', borderRadius: '4px', background: 'var(--surface3)', color: 'var(--text2)', textDecoration: 'none' }}>{a.name}{a.size ? ` (${(a.size / 1024 / 1024).toFixed(1)}MB)` : ''}</a>)}
                          </div> ) : null; } catch { return null; } })()}
                        <a href={rel.html_url} target="_blank" rel="noopener" style={{ fontSize: '0.8rem', color: 'var(--accent)', textDecoration: 'none', display: 'inline-block', marginTop: '6px' }}>查看 Release →</a>
                      </div>
                    )) : <div style={{ textAlign: 'center', padding: '30px', color: 'var(--text2)' }}>暂无 Release</div>;
                  })()}
                </div>
              )}

              {detailTab === 'readme' && (
                <READMEView htmlContent={readmeContent} githubUrl={detailRepo.html_url} loading={readmeLoading} onAskAI={() => openGhChatForRepo(detailRepo)} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ═══ CATEGORY MODAL ═══ */}
      {showCatModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1001, display: 'flex', justifyContent: 'center', alignItems: 'center' }} onClick={() => setShowCatModal(false)}>
          <div style={{ background: 'var(--surface)', borderRadius: '16px', padding: '24px', width: 400, maxWidth: '90vw' }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 16px' }}>{editingCat ? '编辑分类' : '新建分类'}</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '4px', color: 'var(--text2)' }}>名称</label>
                <input value={catName} onChange={e => setCatName(e.target.value)} style={{ width: '100%', padding: '10px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', boxSizing: 'border-box' }} />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '4px', color: 'var(--text2)' }}>关键词（逗号分隔）</label>
                <input value={catKeywords} onChange={e => setCatKeywords(e.target.value)} placeholder="react, vue, web" style={{ width: '100%', padding: '10px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', boxSizing: 'border-box' }} />
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '4px', color: 'var(--text2)' }}>颜色</label>
                  <input type="color" value={catColor} onChange={e => setCatColor(e.target.value)} style={{ width: '100%', height: 40, borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', cursor: 'pointer' }} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '4px', color: 'var(--text2)' }}>图标</label>
                  <input value={catIcon} onChange={e => setCatIcon(e.target.value)} style={{ width: '100%', padding: '10px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', boxSizing: 'border-box' }} />
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
                <button onClick={async () => {
                  const fd = new FormData();
                  fd.append('name', catName);
                  fd.append('keywords', JSON.stringify(catKeywords.split(',').map(s => s.trim()).filter(Boolean)));
                  fd.append('color', catColor);
                  fd.append('icon', catIcon);
                  if (editingCat) {
                    await fetch(`/api/github/categories/${editingCat.id}`, { method: 'PUT', body: fd });
                  } else {
                    await fetch('/api/github/categories', { method: 'POST', body: fd });
                  }
                  showToast(editingCat ? '分类已更新' : '分类已创建', 'success');
                  setShowCatModal(false); loadCategories();
                }} style={{ flex: 1, padding: '12px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', fontWeight: 600, cursor: 'pointer' }}>{editingCat ? '保存' : '创建'}</button>
                <button onClick={() => setShowCatModal(false)} style={{ padding: '12px 20px', borderRadius: '8px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer' }}>取消</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ DISCOVERY DETAIL MODAL ═══ */}
      {discoverDetail && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', zIndex: 1000, display: 'flex', justifyContent: 'center', alignItems: 'center', padding: '20px' }} onClick={() => setDiscoverDetail(null)}>
          <div style={{ background: 'var(--surface)', borderRadius: '16px', width: '100%', maxWidth: 1200, maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }} onClick={e => e.stopPropagation()}>
            <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '14px' }}>
              <img src={`https://github.com/${(discoverDetail.full_name||'').split('/')[0]}.png?size=64`} alt="" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                style={{ width: 52, height: 52, borderRadius: '12px', background: 'var(--surface2)' }} />
              <div style={{ flex: 1 }}>
                <h2 style={{ margin: 0, fontSize: '1.3rem' }}>{discoverDetail.full_name}</h2>
                <p style={{ margin: '4px 0 0', fontSize: '0.9rem', color: 'var(--text2)' }}>{discoverDetail.description}</p>
                <div style={{ display: 'flex', gap: '12px', marginTop: '6px' }}>
                  {discoverDetail.language && <span style={{ fontSize: '0.8rem', color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <span style={{ width: 10, height: 10, borderRadius: '50%', background: LANG_COLORS[discoverDetail.language] || '#8b5cf6' }} />{discoverDetail.language}
                  </span>}
                  {discoverDetail.stargazers_count > 0 && <span style={{ fontSize: '0.8rem', color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '3px' }}><Star size={12} />{formatNum(discoverDetail.stargazers_count)}</span>}
                </div>
              </div>
              <a href={discoverDetail.html_url || `https://github.com/${discoverDetail.full_name}`} target="_blank" rel="noopener" style={{ color: 'var(--accent)', padding: '8px' }}><ExternalLink size={18} /></a>
              <StarButton fullName={discoverDetail.full_name} accounts={ghAccounts} starRepo={starRepo} />
              <button onClick={() => setDiscoverDetail(null)} style={{ background: 'none', border: 'none', color: 'var(--text2)', cursor: 'pointer', padding: '8px' }}><X size={20} /></button>
            </div>
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <READMEView htmlContent={discoverReadme} githubUrl={discoverDetail.html_url || `https://github.com/${discoverDetail.full_name}`} loading={discoverReadmeLoading}
                onAskAI={() => openGhChatForRepo({ full_name: discoverDetail.full_name, description: discoverDetail.description, language: discoverDetail.language, stargazers_count: discoverDetail.stargazers_count })} />
            </div>
          </div>
        </div>
      )}

      {/* ═══ GITHUB AI CHAT PANEL ═══ */}
      {ghChatOpen && (
        <div className="gh-chat-panel" style={{ position: 'fixed', bottom: 0, right: 0, width: 420, maxHeight: '70vh', zIndex: 1001, display: 'flex', flexDirection: 'column',
          background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '16px 16px 0 0', boxShadow: '0 -4px 30px rgba(0,0,0,0.3)',
          margin: '0 24px', animation: 'fadeInUp 0.2s ease' }}>
          {/* Header */}
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '1.1rem' }}>🤖</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: '0.9rem' }}>{ghChatRepo ? ghChatRepo.full_name : '趋势分析'}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text2)' }}>{ghChatRepo?._releaseContext ? 'Release 分析' : ghChatRepo ? '项目问答' : '当前发现频道趋势'}</div>
            </div>
            <button onClick={() => setGhChatOpen(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', cursor: 'pointer', padding: '4px' }}>✕</button>
          </div>
          {/* Messages */}
          <div ref={ghChatMsgsRef} onScroll={() => {
            const el = ghChatMsgsRef.current;
            if (el) ghChatAutoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
          }} style={{ flex: 1, overflowY: 'auto', padding: '14px', display: 'flex', flexDirection: 'column', gap: '10px', minHeight: 200, maxHeight: 400 }}>
            {ghChatMsgs.map((m, i) => (
              <div key={i} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '85%' }}>
                {m.role === 'user' ? (
                  <div style={{ padding: '10px 14px', borderRadius: '12px', fontSize: '0.85rem', lineHeight: 1.6,
                    background: 'var(--accent)', color: '#fff',
                    borderBottomRightRadius: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</div>
                ) : (
                  <div className="markdown-body" style={{ padding: '10px 14px', borderRadius: '12px', fontSize: '0.85rem', lineHeight: 1.6,
                    background: 'var(--surface2)', color: 'var(--text)',
                    borderBottomLeftRadius: 4, border: '1px solid var(--border)', wordBreak: 'break-word' }}
                    dangerouslySetInnerHTML={{ __html: m.content ? sanitize(marked.parse(m.content) as string) : '' }} />
                )}
              </div>
            ))}
            {ghChatLoading && ghChatMsgs[ghChatMsgs.length - 1]?.content === '' && (
              <div style={{ alignSelf: 'flex-start', padding: '10px 14px', borderRadius: '12px', background: 'var(--surface2)', border: '1px solid var(--border)', fontSize: '0.85rem', color: 'var(--text2)' }}>
                <span className="loading-dots">思考中</span>
              </div>
            )}
            <div />
          </div>
          {/* Quick actions */}
          {ghChatMsgs.length <= 1 && (
            <div style={{ padding: '0 14px 8px', display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {(ghChatRepo?._releaseContext
                ? ['最近有什么重要更新？', '总结版本变更', '有哪些 Breaking Changes？']
                : ghChatRepo
                  ? ['这个项目是做什么的？', '技术架构怎么样？', '怎么快速上手？']
                  : ['当前技术热点是什么？', '推荐值得关注的项目', '分析趋势方向']
              ).map(q => (
                <button key={q} onClick={() => sendGhChat(q)} style={{ padding: '5px 10px', borderRadius: '6px', border: '1px solid var(--border)', background: 'transparent', color: 'var(--text2)', cursor: 'pointer', fontSize: '0.78rem' }}>{q}</button>
              ))}
            </div>
          )}
          {/* Input */}
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border)', display: 'flex', gap: '8px' }}>
            <input value={ghChatInput} onChange={e => setGhChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendGhChat(); } }}
              placeholder="输入问题..."
              style={{ flex: 1, padding: '10px 14px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', fontSize: '0.85rem', outline: 'none' }} />
            <button onClick={() => sendGhChat()} disabled={ghChatLoading || !ghChatInput.trim()}
              style={{ padding: '10px 16px', borderRadius: '8px', border: 'none', background: 'var(--accent)', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', opacity: ghChatLoading || !ghChatInput.trim() ? 0.5 : 1 }}>发送</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default App;
