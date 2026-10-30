import { ref } from 'vue'
import { ALLOWED_EXTS } from '@/api/tasks'

// 工作文件夹（V3-A 7.2）：用浏览器的文件夹授权接口选一个本机文件夹，句柄只存在本浏览器的 IndexedDB。
// 文件夹名、路径与其中的文件名不发送到服务器、不写日志、不进地址。

type Mode = { mode: 'readwrite' }
type FolderHandle = FileSystemDirectoryHandle & {
  queryPermission(descriptor: Mode): Promise<PermissionState>
  requestPermission(descriptor: Mode): Promise<PermissionState>
  values(): AsyncIterable<FileSystemHandle>
}
type PickerWindow = Window & { showDirectoryPicker?: (options: { id: string; mode: 'readwrite' }) => Promise<FolderHandle> }

export interface FolderFile { name: string; size: number; modified: number; file: File }

const DB_NAME = 'si', STORE = 'prefs', KEY = 'workFolder'
const WRITE: Mode = { mode: 'readwrite' }

/** 当前选定的文件夹名（只在本页界面显示）；未选或浏览器不支持时为 null。 */
export const folderName = ref<string | null>(null)

/** 浏览器是否支持（Edge / Chrome，需 HTTPS 或 localhost）；不支持时界面不显示任何工作文件夹入口。 */
export const supported = typeof window !== 'undefined' && window.isSecureContext && typeof (window as PickerWindow).showDirectoryPicker === 'function'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => { request.result.createObjectStore(STORE) }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}
async function store<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb()
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = run(db.transaction(STORE, mode).objectStore(STORE))
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  } finally { db.close() }
}

async function readHandle(): Promise<FolderHandle | null> {
  if (!supported) return null
  try { return ((await store('readonly', s => s.get(KEY))) as FolderHandle | undefined) ?? null } catch { return null }
}

/** 读取已记住的文件夹，更新 folderName；页面载入时调用。 */
export async function current(): Promise<FolderHandle | null> {
  const handle = await readHandle()
  folderName.value = handle?.name ?? null
  return handle
}

/** 选择文件夹（须在用户点击中调用）。取消选择返回 false。 */
export async function pick(): Promise<boolean> {
  const picker = (window as PickerWindow).showDirectoryPicker
  if (!picker) return false
  try {
    const handle = await picker.call(window, { id: 'si-work', mode: 'readwrite' })
    await store('readwrite', s => s.put(handle, KEY))
    folderName.value = handle.name
    return true
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') return false
    throw e
  }
}

export async function clear(): Promise<void> {
  try { await store('readwrite', s => s.delete(KEY)) } catch { /* 删不掉只影响下次打开 */ }
  folderName.value = null
}

const DENIED = '没有该文件夹的访问权限，请重新选择'

/** 确认已授权；未授权且 ask 为真时发起授权（须在用户点击中）。 */
async function authorized(handle: FolderHandle, ask: boolean): Promise<boolean> {
  try {
    if (await handle.queryPermission(WRITE) === 'granted') return true
    return ask && await handle.requestPermission(WRITE) === 'granted'
  } catch { return false }
}

/** 列出文件夹第一层中支持格式的文件，按修改时间倒序（须在用户点击中调用）。只读取元数据，不上传。 */
export async function listFiles(): Promise<FolderFile[]> {
  const handle = await readHandle()
  if (!handle) throw new Error(DENIED)
  if (!await authorized(handle, true)) throw new Error(DENIED)
  const out: FolderFile[] = []
  for await (const entry of handle.values()) {
    if (entry.kind !== 'file') continue
    const ext = entry.name.split('.').pop()?.toLowerCase() ?? ''
    if (!ALLOWED_EXTS.includes(ext) || entry.name.startsWith('~$')) continue
    const file = await (entry as FileSystemFileHandle).getFile()
    out.push({ name: entry.name, size: file.size, modified: file.lastModified, file })
  }
  return out.sort((a, b) => b.modified - a.modified)
}

async function exists(dir: FileSystemDirectoryHandle, name: string): Promise<boolean> {
  try { await dir.getFileHandle(name); return true }
  catch (e) { if (e instanceof DOMException && e.name === 'NotFoundError') return false; throw e }
}

/** 把导出文件写入工作文件夹；同名时依次改为“名称 (2).docx”，绝不覆盖。返回实际文件名；没有可用的工作文件夹时返回 null。 */
export async function saveInto(blob: Blob, filename: string): Promise<string | null> {
  const handle = await readHandle()
  if (!handle || !await authorized(handle, true)) return null
  const dot = filename.lastIndexOf('.')
  const base = dot > 0 ? filename.slice(0, dot) : filename
  const ext = dot > 0 ? filename.slice(dot) : ''
  let name = filename
  for (let n = 2; await exists(handle, name); n++) name = `${base} (${n})${ext}`
  const writable = await (await handle.getFileHandle(name, { create: true })).createWritable()
  try { await writable.write(blob); await writable.close() }
  catch (e) { try { await writable.abort() } catch { /* 已失败 */ } throw e }
  return name
}
