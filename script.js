/**
 * TomatoCat Agent — GitHub Pages 展示页交互脚本
 * 主题切换 + 滚动显示 + 平滑滚动
 */

// ========================================
// 主题切换系统
// ========================================
class ThemeToggle {
    constructor() {
        this.toggle = document.getElementById('themeToggle');
        this.icon = this.toggle.querySelector('.theme-icon');
        this.html = document.documentElement;
        
        this.init();
    }
    
    init() {
        const savedTheme = localStorage.getItem('theme') || 'light';
        this.applyTheme(savedTheme);
        
        this.toggle.addEventListener('click', () => this.toggleTheme());
    }
    
    toggleTheme() {
        const current = this.html.getAttribute('data-theme') || 'light';
        const next = current === 'light' ? 'dark' : 'light';
        this.applyTheme(next);
        localStorage.setItem('theme', next);
    }
    
    applyTheme(theme) {
        this.html.setAttribute('data-theme', theme);
        this.icon.textContent = theme === 'light' ? '🌙' : '☀️';
    }
}

// ========================================
// 滚动显示动画
// ========================================
class ScrollReveal {
    constructor() {
        this.elements = document.querySelectorAll('.reveal');
        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    this.observer.unobserve(entry.target);
                }
            });
        }, {
            threshold: 0.1,
            rootMargin: '0px 0px -40px 0px'
        });
        
        this.init();
    }
    
    init() {
        this.elements.forEach((el, index) => {
            const parent = el.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(child => 
                    child.classList.contains('reveal')
                );
                const siblingIndex = siblings.indexOf(el);
                if (siblingIndex > 0) {
                    el.style.transitionDelay = `${siblingIndex * 0.08}s`;
                }
            }
            
            this.observer.observe(el);
        });
    }
}

// ========================================
// 导航栏滚动效果
// ========================================
class NavbarEffect {
    constructor() {
        this.header = document.querySelector('.site-header');
        this.init();
    }
    
    init() {
        window.addEventListener('scroll', () => this.handleScroll());
    }
    
    handleScroll() {
        const currentScroll = window.pageYOffset;
        
        if (currentScroll > 30) {
            this.header.style.boxShadow = '0 4px 24px rgba(0, 0, 0, 0.06)';
        } else {
            this.header.style.boxShadow = 'none';
        }
    }
}

// ========================================
// 浮动背景跟随鼠标
// ========================================
class FloatingBackground {
    constructor() {
        this.shapes = document.querySelectorAll('.float-shape');
        this.init();
    }
    
    init() {
        document.addEventListener('mousemove', (e) => {
            const x = e.clientX / window.innerWidth;
            const y = e.clientY / window.innerHeight;
            
            this.shapes.forEach((shape, index) => {
                const speed = (index + 1) * 10;
                const offsetX = (x - 0.5) * speed;
                const offsetY = (y - 0.5) * speed;
                
                shape.style.transform = `translate(${offsetX}px, ${offsetY}px)`;
            });
        });
    }
}

// ========================================
// 平滑滚动
// ========================================
class SmoothScroll {
    constructor() {
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', (e) => {
                e.preventDefault();
                const target = document.querySelector(anchor.getAttribute('href'));
                if (target) {
                    const headerOffset = 72;
                    const elementPosition = target.getBoundingClientRect().top;
                    const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

                    window.scrollTo({
                        top: offsetPosition,
                        behavior: 'smooth'
                    });
                }
            });
        });
    }
}

// ========================================
// 初始化所有系统
// ========================================
document.addEventListener('DOMContentLoaded', () => {
    new ThemeToggle();
    new ScrollReveal();
    new NavbarEffect();
    new FloatingBackground();
    new SmoothScroll();
    
    console.log('🍅🐱 TomatoCat Agent 展示页已加载');
});
