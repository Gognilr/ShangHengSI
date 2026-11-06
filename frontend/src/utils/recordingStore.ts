// 现场录音的本机防丢存储（V31-U5F-05）：录音分片实时写入浏览器 IndexedDB，页面刷新或崩溃后可恢复；
// 上传成功后清除。数据只在本机浏览器，不经服务器。
const DB = 'si-recording', CHUNKS = 'chunks', META = 'meta', KEY = 'current'

export interface RecordingMeta {
  startedAt: number; samples: number; marks: number[]; topic: string; participants: string[]
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => { request.result.createObjectStore(CHUNKS, { autoIncrement: true }); request.result.createObjectStore(META) }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}
function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => { tx.oncomplete = () => resolve(); tx.onerror = tx.onabort = () => reject(tx.error) })
}

// 写入串成一条链，保证分片顺序；任何一次失败只吞掉（存储满、隐私模式），录音本身继续在内存里正常进行
let chain: Promise<unknown> = Promise.resolve()
function enqueue<T>(job: () => Promise<T>): Promise<T | undefined> {
  const next = chain.then(job, job).catch(() => undefined)
  chain = next
  return next
}

export const recordingStore = {
  supported: typeof indexedDB !== 'undefined',
  /** 开始新录音：清掉旧分片，写入元信息 */
  begin: (meta: RecordingMeta) => enqueue(async () => {
    const db = await open(), tx = db.transaction([CHUNKS, META], 'readwrite')
    tx.objectStore(CHUNKS).clear(); tx.objectStore(META).put(meta, KEY)
    await done(tx); db.close()
  }),
  append: (chunk: Int16Array, meta: RecordingMeta) => enqueue(async () => {
    const db = await open(), tx = db.transaction([CHUNKS, META], 'readwrite')
    tx.objectStore(CHUNKS).add(chunk.buffer); tx.objectStore(META).put(meta, KEY)
    await done(tx); db.close()
  }),
  saveMeta: (meta: RecordingMeta) => enqueue(async () => {
    const db = await open(), tx = db.transaction(META, 'readwrite')
    tx.objectStore(META).put(meta, KEY)
    await done(tx); db.close()
  }),
  meta: () => enqueue(async () => {
    const db = await open(), tx = db.transaction(META, 'readonly')
    const request = tx.objectStore(META).get(KEY)
    await done(tx); db.close()
    return request.result as RecordingMeta | undefined
  }),
  chunks: () => enqueue(async () => {
    const db = await open(), tx = db.transaction(CHUNKS, 'readonly')
    const request = tx.objectStore(CHUNKS).getAll()
    await done(tx); db.close()
    return (request.result as ArrayBuffer[]).map(b => new Int16Array(b))
  }),
  clear: () => enqueue(async () => {
    const db = await open(), tx = db.transaction([CHUNKS, META], 'readwrite')
    tx.objectStore(CHUNKS).clear(); tx.objectStore(META).clear()
    await done(tx); db.close()
  }),
}
