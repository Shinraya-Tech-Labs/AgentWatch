import { MetadataRoute } from 'next';
import { BLOG_POSTS } from './blog/data';

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = 'https://agentwatch.ai'; // Replace with actual production URL

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
