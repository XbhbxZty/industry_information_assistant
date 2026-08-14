// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
import classNames from 'classnames'
import { Marked, Renderer, TokenizerAndRendererExtension } from 'marked'
import { useMemo } from 'react'
import './index.scss'

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
}) {
  const { value, extensions, className, gfm, ...otherProps } = props

  const html = useMemo(() => {
    const renderer = new Renderer()

    // 自定义图片渲染：只渲染有效的图片 URL，隐藏无效的图片引用
    renderer.image = ({ href, title, text }: { href: string; title: string | null; text: string }) => {
      // 只渲染 base64 data URL 或有效的 http(s) URL
      if (href && (href.startsWith('data:image/') || href.startsWith('http://') || href.startsWith('https://'))) {
        const titleAttr = title ? ` title="${title}"` : ''
        return `<img src="${href}" alt="${text || ''}"${titleAttr} class="markdown-image" loading="lazy" />`
      }
      // 无效的图片 URL，直接隐藏（实际图表在"可视化图表"tab中显示）
      return ''
    }

    const marked = new Marked({
      extensions: props.extensions,
    })
    const html = marked.parse(props.value ?? '', {
      gfm: props.gfm ?? false,
      renderer,
    })

    return html
  }, [value, extensions, gfm])

  return (
    <div
      className={classNames('com-markdown', className)}
      {...otherProps}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
