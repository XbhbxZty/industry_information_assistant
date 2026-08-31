// Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
// 未经授权，禁止转售或仿制。
//
// 本文件在原课程项目基础上二次开发（已获授权）。
// 改造部分 © 2026 XbhbxZty
import { AuthGuard } from '@/components/auth-guard'
import { RoleGuard } from '@/components/auth-guard/role-guard'
import { BaseLayout } from '@/layout/base'
import NotFound from '@/pages/404'
import LoginPage from '@/pages/auth/login'
import Chat from '@/pages/chat'
import NewChat from '@/pages/chat/newchat'
import Index from '@/pages/index'
import KnowledgePage from '@/pages/knowledge'
import MemoryPage from '@/pages/memory'
import DatabasePage from '@/pages/database'
import DueDiligencePage from '@/pages/due-diligence'
import NewsPage from '@/pages/news'
import BiddingPage from '@/pages/bidding'
import CompanyProfileListPage from '@/pages/company-profiles/list'
import CompanyProfileDetailPage from '@/pages/company-profiles/detail'
import CompanyProfileEditorPage from '@/pages/company-profiles/editor'
import RiskReviewPage from '@/pages/risk-reviews'
import { ReviewerGuard } from '@/components/auth-guard/reviewer-guard'
import {
  Navigate,
  Outlet,
  RouteObject,
  createBrowserRouter,
} from 'react-router-dom'

export type IRouteObject = {
  children?: IRouteObject[]
  name?: string
  auth?: boolean
  pure?: boolean
  meta?: unknown
} & Omit<RouteObject, 'children'>

export const routes: IRouteObject[] = [
  {
    path: '/',
    Component: Index,
  },
  {
    path: '/due-diligence',
    Component: DueDiligencePage,
  },
  {
    path: '/chat',
    children: [
      {
        path: '',
        Component: NewChat,
      },
      {
        path: ':id',
        Component: Chat,
      },
    ],
  },
  {
    path: '/knowledge',
    Component: KnowledgePage,
  },
  {
    path: '/memory',
    Component: MemoryPage,
  },
  {
    path: '/database',
    element: (
      <RoleGuard
        subTitle="数据库探索包含受控的内部业务数据，仅限管理员使用。"
        returnTo="/"
        returnLabel="返回首页"
      >
        <DatabasePage />
      </RoleGuard>
    ),
  },
  {
    path: '/news',
    Component: NewsPage,
  },
  {
    path: '/bidding',
    Component: BiddingPage,
  },
  {
    path: '/company-profiles',
    Component: CompanyProfileListPage,
  },
  {
    path: '/company-profiles/new',
    element: <RoleGuard><CompanyProfileEditorPage /></RoleGuard>,
  },
  {
    path: '/company-profiles/:id/edit',
    element: <RoleGuard><CompanyProfileEditorPage /></RoleGuard>,
  },
  {
    path: '/company-profiles/:id',
    Component: CompanyProfileDetailPage,
  },
  {
    path: '/risk-reviews',
    element: <ReviewerGuard><RiskReviewPage /></ReviewerGuard>,
  },
  {
    path: '/404',
    Component: NotFound,
    pure: true,
  },
]

export const router = createBrowserRouter(
  [
    {
      path: '/login',
      element: <LoginPage />,
    },
    {
      path: '/',
      element: (
        <AuthGuard>
          <BaseLayout>
            <Outlet />
          </BaseLayout>
        </AuthGuard>
      ),
      children: routes,
    },
    {
      path: '*',
      element: <Navigate to="/404" />,
    },
  ] as RouteObject[],
  {
    basename: import.meta.env.BASE_URL,
  },
)
