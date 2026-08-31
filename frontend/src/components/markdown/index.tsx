// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
import classNames from 'classnames'
import { Marked, Renderer, TokenizerAndRendererExtension } from 'marked'
import { useMemo } from 'react'
import './index.scss'

function escapeHtml(value: string) {
  return value.replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char] || char)
}

export default function Markdown(props: {
  className?: string
  value?: string
  extensions?: TokenizerAndRendererExtension[]
  /**
   * 是否启用 GitHub 风格 Markdown（表格、删除线等）。
   *
   * 默认 false 以保持既有页面的渲染行为不变；尽调报告里的风险评级块、
   * 授信额度测算与证据溯源附录都是表格，必须开启才能读。
   */
  gfm?: boolean
  /**
   * 跨用户内容必须启用：保留 Markdown 排版，但禁用原始 HTML、危险链接
   * 协议和可注入的属性。默认关闭以避免改变既有同用户页面的渲染契约。
   */
  safe?: boolean
}) {
  const { value, extensions, className, gfm, safe, ...otherProps } = props

  const html = useMemo(() => {
    const renderer = new Renderer()

    if (safe) {
      renderer.html = ({ text }: { text: string }) => escapeHtml(text)
      renderer.link = ({ href, title, text }: { href: string; title?: string | null; text: string }) => {
        if (!href.startsWith('https://') && !href.startsWith('http://')) return escapeHtml(text)
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
        return `<a href="${escapeHtml(href)}"${titleAttr} target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`
      }
    }

    // 自定义图片渲染：只渲染有效的图片 URL，隐藏无效的图片引用
    renderer.image = ({ href, title, text }: { href: string; title: string | null; text: string }) => {
      if (safe) {
        const safeRasterData = /^data:image\/(?:png|jpeg|gif|webp);base64,/i.test(href)
        const safeRemote = href.startsWith('https://') || href.startsWith('http://')
        if (!safeRasterData && !safeRemote) return ''
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
        return `<img src="${escapeHtml(href)}" alt="${escapeHtml(text || '')}"${titleAttr} class="markdown-image" loading="lazy" />`
      }
      // 只渲染 base64 data URL 或有效的 http(s) URL
      if (href && (href.startsWith('data:image/') || href.startsWith('http://') || href.startsWith('https://'))) {
        const titleAttr = title ? ` title="${title}"` : ''
        return `<img src="${href}" alt="${text || ''}"${titleAttr} class="markdown-image" loading="lazy" />`
      }
      // 无效的图片 URL，直接隐藏（实际图表在"可视化图表"tab中显示）
      return ''
    }

    const marked = new Marked({
      extensions,
    })
    const html = marked.parse(value ?? '', {
      gfm: gfm ?? false,
      renderer,
    })

    return html
  }, [value, extensions, gfm, safe])

  return (
    <div
      className={classNames('com-markdown', className)}
      {...otherProps}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
