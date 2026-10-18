# RealEstAI

Transform property footage into interactive, navigable 3D virtual property tours.

## Overview

RealEstAI is a SaaS platform that enables real estate agents to upload ordinary property videos or 360-degree footage and generate realistic, interactive 3D property experiences. Buyers can explore reconstructed properties in first-person or third-person mode, with keyboard, mouse, and mobile controls.

## Architecture

### Frontend
- **Framework**: Next.js 15 with App Router
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **3D Rendering**: Three.js with React Three Fiber
- **Authentication**: Supabase Auth

### Backend
- **Framework**: FastAPI (Python)
- **Database**: PostgreSQL via Supabase
- **ORM**: SQLAlchemy (async)
- **Migrations**: Alembic
- **Storage**: Supabase Storage (configurable for S3)
- **Job Queue**: Redis + Celery (for future GPU processing)

## Prerequisites

- Node.js 18+ (for frontend)
- Python 3.11+ (for backend)
- PostgreSQL database (via Supabase recommended)
- Supabase project account

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd realestai
```

### 2. Backend Setup

#### 2.1 Create Virtual Environment

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

#### 2.2 Install Dependencies

```bash
pip install -r requirements.txt
```

#### 2.3 Configure Environment Variables

Create a `.env` file in the `backend` directory:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```env
# Application
APP_NAME=RealEstAI
APP_VERSION=1.0.0
DEBUG=true

# Database (PostgreSQL connection string)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/realestai

# Supabase Configuration
SUPABASE_URL=your_supabase_project_url
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key

# Storage
STORAGE_BUCKET=property-videos
STORAGE_REGION=us-east-1

# Redis for job queue (optional)
REDIS_URL=redis://localhost:6379/0

# Security
SECRET_KEY=your_secret_key_here_change_in_production
ACCESS_TOKEN_EXPIRE_MINUTES=30

# CORS
FRONTEND_URL=http://localhost:3000
```

#### 2.4 Run Database Migrations

```bash
alembic upgrade head
```

#### 2.5 Start the Backend Server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

API documentation: `http://localhost:8000/docs`

### 3. Frontend Setup

#### 3.1 Install Dependencies

```bash
cd frontend
npm install
```

#### 3.2 Configure Environment Variables

Create a `.env.local` file in the `frontend` directory:

```bash
cp env.example .env.local
```

Edit `.env.local` with your configuration:

```env
# Supabase Configuration
NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key

# Backend API URL
NEXT_PUBLIC_API_URL=http://localhost:8000
```

#### 3.3 Start the Development Server

```bash
npm run dev
```

The application will be available at `http://localhost:3000`

## Database Schema

The database includes the following tables:

- **users**: User profiles synced with Supabase Auth
- **properties**: Property listings with details
- **property_media**: Uploaded videos and images
- **processing_jobs**: 3D reconstruction job tracking
- **buyer_inquiries**: Buyer contact form submissions

### Key Relationships

- Properties belong to users (owner_id)
- Media belongs to properties
- Processing jobs belong to properties and media
- Inquiries belong to properties

## API Endpoints

### Properties
- `POST /properties/` - Create a new property
- `GET /properties/` - List properties (with optional owner filter)
- `GET /properties/{id}` - Get property details
- `PUT /properties/{id}` - Update property
- `DELETE /properties/{id}` - Delete property

### Upload
- `POST /upload/property-media` - Upload property media (video/images)
- `GET /upload/media/{id}` - Get media metadata

### Processing Jobs
- `POST /processing/jobs` - Create a processing job
- `GET /processing/jobs/{id}` - Get job status
- `GET /processing/jobs` - List jobs (with optional filters)
- `PUT /processing/jobs/{id}` - Update job status

## Authentication

Authentication is handled by Supabase Auth. The frontend uses Supabase client SDK for sign-up, sign-in, and session management. The backend validates JWT tokens from the Authorization header.

### Protected Routes

Frontend routes are protected by middleware:
- `/dashboard` - Requires authentication
- `/properties/*` - Requires authentication for property management

## File Upload

### Supported Formats

**Videos**: MP4, MOV, AVI
**Images**: JPEG, PNG (including 360° images)

### File Size Limits

Maximum file size: 500MB

### Storage

Files are stored in Supabase Storage (configurable for S3). The backend validates file types and sizes before upload.

## Processing Jobs

Processing jobs track the 3D reconstruction pipeline:

### Job States

1. `uploaded` - File uploaded successfully
2. `queued` - Job queued for processing
3. `processing` - Reconstruction in progress
4. `needs_review` - Processing complete, awaiting review
5. `published` - Tour published and live
6. `failed` - Processing failed with error

### Current Status

The job infrastructure is in place but actual 3D reconstruction requires:
- GPU processing pipeline
- AI/ML model integration
- Worker process implementation

Jobs will remain in `uploaded` state until the reconstruction pipeline is configured.

## 3D Viewer

The 3D viewer uses Three.js with React Three Fiber. It supports:

- Mouse controls (rotate, pan, zoom)
- First-person and third-person view modes
- GLB/GLTF model loading
- Responsive design
- Loading states and error handling

### Demo Mode

When no property model is available, the viewer displays a sample 3D model for demonstration purposes.

## Testing

### Backend Tests

```bash
cd backend
pytest
```

### Frontend Tests

```bash
cd frontend
npm test
```

## Building for Production

### Backend

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm run build
npm start
```

## Deployment

### Backend Deployment

Recommended platforms:
- Render
- Railway
- AWS EC2
- DigitalOcean

Ensure environment variables are set in production.

### Frontend Deployment

Recommended platforms:
- Vercel (recommended for Next.js)
- Netlify
- AWS Amplify

Set environment variables in the deployment platform.

## Security Considerations

- Never commit `.env` files or secrets
- Use HTTPS in production
- Validate all file uploads on the server
- Implement rate limiting on API endpoints
- Use Supabase Row Level Security (RLS) policies
- Keep dependencies updated

## Known Limitations

1. **3D Reconstruction**: The AI reconstruction pipeline is not yet implemented. Jobs will remain in `uploaded` state.
2. **File Storage**: Currently uses local storage for development. Configure Supabase Storage or S3 for production.
3. **Authentication**: Backend token validation is simplified. Implement full JWT validation with Supabase.
4. **Worker Queue**: Redis/Celery integration is prepared but not yet active.

## Next Steps for Phase 2

To integrate the real AI property reconstruction pipeline:

1. **Implement GPU Processing Pipeline**
   - Set up GPU server or use cloud GPU service
   - Implement neural radiance fields (NeRF) or Gaussian splatting
   - Create worker process with Celery

2. **Connect Reconstruction Service**
   - Integrate reconstruction model with processing jobs
   - Handle async job updates
   - Generate GLB/GLTF output files

3. **Enhance 3D Viewer**
   - Add collision detection
   - Implement navigation mesh
   - Add room-based navigation
   - Integrate floor plan view

4. **Production Hardening**
   - Implement full JWT validation
   - Add rate limiting
   - Set up monitoring and logging
   - Configure CDN for static assets

## Support

For issues or questions, please refer to the project documentation or contact the development team.

## License

[Add your license here]
