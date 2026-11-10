import { MetadataRoute } from 'next';

export const dynamic = 'force-static';
import { BLOG_POSTS } from './blog/data';

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl =
    process.env.NEXT_PUBLIC_SITE_URL ?? 'https://shinraya-tech-labs.github.io/AgentWatch';

  const staticRoutes = ['', '/about', '/blog', '/contributors'].map((route) => ({
    url: `${baseUrl}${route}`,
    changeFrequency: 'weekly' as const,
    priority: route === '' ? 1 : 0.8,
  }));

  const dynamicBlogRoutes = BLOG_POSTS.map((post) => ({
    url: `${baseUrl}/blog/${post.slug}`,
    lastModified: new Date(post.date), // Using post date as last modified
    changeFrequency: 'monthly' as const,
    priority: 0.6,
  }));

  return [...staticRoutes, ...dynamicBlogRoutes];
}
