// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
/**
 * Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
 * 未经授权，禁止转售或仿制。
 */

import { PageTransportKey } from '@/utils'

export type ChatEnterData = {
  message: string
}

export const transportToChatEnter = Symbol() as PageTransportKey<{
  data: ChatEnterData
}>

let id = 0

export const createChatId = () => {
  return ++id
}

export function createChatIdText(id: number) {
  return `chat-item-${id}`
}
