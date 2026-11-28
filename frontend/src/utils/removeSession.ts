import { ElMessageBox } from 'element-plus'
import { assistantApi, type SessionItem } from '@/api/assistant'

/**
 * 确认并删除会话。文件多的会话后端要删原件、级联删节点与对照表，可能要几秒：
 * 确认框在删除完成前保持打开、按钮转圈，期间不能取消或重复点；删完才关闭。
 * 返回 true 表示已删除；用户取消返回 false；删除失败抛出原错误。
 */
export async function confirmRemoveSession(item: SessionItem): Promise<boolean> {
  let failure: unknown, busy = false
  try {
    await ElMessageBox.confirm(`将删除“${item.title}”及其文件、消息与对照表，无法恢复。`, '删除会话', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
      beforeClose: async (action, instance, done) => {
        if (busy) return
        if (action !== 'confirm') { done(); return }
        busy = true
        instance.confirmButtonLoading = true; instance.confirmButtonText = '删除中…'; instance.showCancelButton = false
        try { await assistantApi.remove(item.id) } catch (e) { failure = e }
        busy = false; done()
      },
    })
  } catch { return false }
  if (failure) throw failure
  return true
}
