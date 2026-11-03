import type { InjectionKey } from 'vue'
export interface SettingAnchor { id: string; title: string; element: HTMLElement }
export const settingSectionsKey: InjectionKey<{ add: (section: SettingAnchor) => void; remove: (id: string) => void }> = Symbol('settingSections')
