// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
/// <reference types="vite/client" />

interface Window {
  $app: import('antd/es/app/context').useAppProps
  $showLoading: (options?: { title?: string }) => void
  $hideLoading: () => void
}
