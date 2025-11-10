 # CAP101-SupplyTrack

## Setup and Installation

### 1. Clone the Repository and Install Dependencies
```bash
git clone https://github.com/Jomarie0/E-COMMERCE-SUPPLYTRACK.git
cd E-Commerce-Website

# On Windows
python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt

# Database Setup
python manage.py makemigrations
python manage.py migrate

# Create Superuser
python manage.py createsuperuser

# Run Development Server
python manage.py runserver
```

## Git Requirements
1. Clone Project
```bash
git clone https://github.com/Jomarie0/E-COMMERCE-SUPPLYTRACK.git
```

2. Push Code
```bash
git add .
git commit -m "Message and Changes"
git push
```

3. Pull Project
```bash
git fetch
git pull
```

4. Create Branch
```bash
git branch <branch_name>
git checkout <branch_name>
