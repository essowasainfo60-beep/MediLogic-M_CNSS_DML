import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import hashlib
from dateutil.relativedelta import relativedelta

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'MediLogic_Secret_2026')
CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ==================== FONCTIONS UTILITAIRES ====================
def calculer_mois_retard(id_membre):
    """Calcule les mois de retard depuis janvier de l'année courante"""
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT id, nom FROM membres WHERE id = %s", (id_membre,))
    membre = cur.fetchone()
    if not membre:
        return {'mois_retard': 0, 'dette_totale': 0}
    
    annee = datetime.now().year
    mois_courant = datetime.now().month
    jour_courant = datetime.now().day
    
    # Récupérer tous les mois payés
    cur.execute("""
        SELECT generate_series(
            mois_debut, 
            mois_fin, 
            '1 month'::interval
        ) as mois_paye
        FROM cotisations
        WHERE id_membre = %s AND EXTRACT(YEAR FROM mois_debut) = %s
    """, (id_membre, annee))
    
    mois_payes = set()
    for row in cur.fetchall():
        if row['mois_paye']:
            mois_payes.add(int(row['mois_paye'].month))
    
    cur.close()
    conn.close()
    
    # Calculer les mois impayés
    mois_impayes = []
    for mois in range(1, mois_courant + 1):
        if mois not in mois_payes:
            if mois == mois_courant and jour_courant <= 5:
                continue  # Pas encore en retard
            mois_impayes.append(mois)
    
    dette_totale = len(mois_impayes) * 1000
    
    return {'mois_retard': len(mois_impayes), 'dette_totale': dette_totale}

# ==================== PAGES ====================
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/inscription')
def inscription_page():
    return render_template('inscription.html')

@app.route('/membres')
def membres_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('membres.html')

@app.route('/cotisations')
def cotisations_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('cotisations.html')

@app.route('/caisse')
def caisse_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('caisse.html')

@app.route('/historique')
def historique_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('historique.html')

# ==================== API AUTHENTIFICATION ====================
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    hash_mdp = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin WHERE username = %s AND password_hash = %s", (username, hash_mdp))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    
    if admin:
        session['user_id'] = 0
        session['user_nom'] = admin.get('nom_complet') or username  # ← Utiliser nom_complet
        session['user_role'] = 'admin'
        session['is_super_admin'] = admin.get('is_super_admin', False)
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/api/membre/login', methods=['POST'])
def api_membre_login():
    data = request.json
    telephone = data.get('telephone')
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nom, role FROM membres WHERE telephone LIKE %s", (f'%{telephone}%',))
    membre = cur.fetchone()
    cur.close()
    conn.close()
    
    if membre:
        session['user_id'] = membre['id']
        session['user_nom'] = membre['nom']
        session['user_role'] = membre.get('role', 'membre')
        
        # Rediriger vers le bon dashboard selon le rôle
        if session['user_role'] == 'admin':
            return jsonify({'success': True, 'nom': membre['nom'], 'role': 'admin', 'redirect': '/'})
        else:
            return jsonify({'success': True, 'nom': membre['nom'], 'role': 'membre', 'redirect': '/membre'})
    
    return jsonify({'success': False, 'message': 'Numéro non trouvé'})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/session', methods=['GET'])
def get_session():
    if 'user_id' in session:
        return jsonify({
            'logged_in': True,
            'nom': session.get('user_nom'),
            'role': session.get('user_role'),
            'is_super_admin': session.get('is_super_admin', False)
        })
    return jsonify({'logged_in': False})

# ==================== API INSCRIPTION ====================
@app.route('/api/inscription', methods=['POST'])
def api_inscription():
    data = request.json
    nom = data.get('nom')
    telephone = data.get('telephone')
    
    if not nom or not telephone:
        return jsonify({'success': False, 'message': 'Champs requis'})
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier si existe déjà
    cur.execute("SELECT id FROM membres WHERE telephone = %s", (telephone,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Ce numéro existe déjà'})
    
    # Ajouter membre avec frais d'adhésion
    cur.execute("""
        INSERT INTO membres (nom, telephone, frais_adhesion, frais_adhesion_paye, frais_adhesion_reste, adhesion_statut)
        VALUES (%s, %s, 4000, 0, 4000, 'impaye')
        RETURNING id
    """, (nom, telephone))
    membre_id = cur.fetchone()['id']
    
    # Enregistrer le paiement de l'adhésion si payé maintenant
    # (par défaut, impayé - à payer plus tard)
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Inscription réussie'})

# ==================== API MEMBRES ====================
@app.route('/api/membres', methods=['GET'])
def get_membres():
    conn = get_db()
    cur = conn.cursor()
    
    # Pour admin : récupérer toutes les colonnes
    if session.get('user_role') == 'admin':
        cur.execute("""
            SELECT id, nom, telephone, date_inscription, 
                   adhesion_statut, frais_adhesion_paye, frais_adhesion_reste,
                   role
            FROM membres 
            ORDER BY id DESC
        """)
    else:
        cur.execute("""
            SELECT id, nom, telephone, adhesion_statut, frais_adhesion_reste 
            FROM membres 
            WHERE role != 'admin'
        """)
    
    membres = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(membres)

@app.route('/api/membres', methods=['POST'])
def add_membre():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    data = request.json
    nom = data.get('nom')
    telephone = data.get('telephone')
    frais = data.get('frais_adhesion', 4000)
    date_inscription = data.get('date_inscription', datetime.now().strftime('%Y-%m-%d'))
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT id FROM membres WHERE telephone = %s", (telephone,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Ce numéro existe déjà'})
    
    cur.execute("""
        INSERT INTO membres (nom, telephone, date_inscription, frais_adhesion, frais_adhesion_paye, frais_adhesion_reste, adhesion_statut, role)
        VALUES (%s, %s, %s, %s, 0, %s, 'impaye', 'membre')
        RETURNING id
    """, (nom, telephone, date_inscription, frais, frais))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Membre ajouté'})

@app.route('/api/membres/<int:id>', methods=['DELETE'])
def delete_membre(id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM cotisations WHERE id_membre = %s", (id,))
        cur.execute("DELETE FROM caisse WHERE source = %s", (str(id),))
        cur.execute("DELETE FROM membres WHERE id = %s", (id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/api/membre/statut/<int:id>', methods=['GET'])
def get_membre_statut(id):
    retard = calculer_mois_retard(id)
    return jsonify({
        'actuel': 'À jour' if retard['mois_retard'] == 0 else 'En retard',
        'mois_retard': retard['mois_retard'],
        'dette': retard['dette_totale']
    })

@app.route('/api/membres/retard', methods=['GET'])
def get_retard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nom, telephone FROM membres WHERE role != 'admin'")
    membres = cur.fetchall()
    cur.close()
    conn.close()
    
    resultat = []
    for m in membres:
        retard = calculer_mois_retard(m['id'])
        if retard['mois_retard'] > 0:
            resultat.append({
                'id': m['id'],
                'nom': m['nom'],
                'telephone': m['telephone'],
                'mois_retard': retard['mois_retard'],
                'dette_totale': retard['dette_totale']
            })
    
    return jsonify(resultat)

def verifier_periode_deja_payee(id_membre, debut, fin):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM cotisations 
        WHERE id_membre = %s 
        AND (mois_debut BETWEEN %s AND %s OR mois_fin BETWEEN %s AND %s)
    """, (id_membre, debut, fin, debut, fin))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

# ==================== API COTISATIONS ====================
@app.route('/api/cotisations', methods=['POST'])
def add_cotisation():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Seul l\'admin peut enregistrer'}), 403
    
    data = request.json
    id_membre = data['id_membre']
    periode = data['periode']  # 1, 3, 6, 12
    mois_debut = data['mois_debut']  # format: '2026-01'
    montant_unitaire = data.get('montant_unitaire', 1000)
    
    # Utiliser le nom de l'admin connecté automatiquement
    enregistre_par = session.get('user_nom', 'admin')
    
    # Calculer les dates
    debut = datetime.strptime(mois_debut + '-01', '%Y-%m-%d')
    # Ajouter le nombre de mois et reculer d'un jour pour avoir le dernier jour
    fin = debut + relativedelta(months=periode) - timedelta(days=1)
    
    montant_total = periode * montant_unitaire
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier si la période chevauche un paiement existant
    cur.execute("""
        SELECT id, mois_debut, mois_fin, nombre_mois 
        FROM cotisations 
        WHERE id_membre = %s 
        AND (
            (mois_debut <= %s AND mois_fin >= %s) OR
            (mois_debut BETWEEN %s AND %s) OR
            (mois_fin BETWEEN %s AND %s)
        )
    """, (id_membre, fin, debut, debut, fin, debut, fin))
    
    existant = cur.fetchone()
    if existant:
        cur.close()
        conn.close()
        return jsonify({
            'success': False, 
            'message': f'⚠️ Période déjà couverte ! Déjà payé du {existant["mois_debut"].strftime("%B %Y")} au {existant["mois_fin"].strftime("%B %Y")}'
        })
    
    # Enregistrer la cotisation
    cur.execute("""
        INSERT INTO cotisations (id_membre, mois_debut, mois_fin, nombre_mois, montant_total, montant_unitaire, enregistre_par)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (id_membre, debut, fin, periode, montant_total, montant_unitaire, enregistre_par))
    
    # Alimenter la caisse
    cur.execute("SELECT COALESCE(solde_apres, 0) FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    solde_actuel = solde['coalesce'] if solde else 0
    nouveau_solde = solde_actuel + montant_total
    
    cur.execute("""
        INSERT INTO caisse (type, montant, motif, source, solde_apres, effectue_par)
        VALUES ('entree', %s, %s, %s, %s, %s)
    """, (montant_total, f"Cotisation {periode} mois à partir de {mois_debut}", str(id_membre), nouveau_solde, enregistre_par))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({
        'success': True, 
        'message': f'{montant_total} FCFA enregistrés pour {periode} mois (du {debut.strftime("%B %Y")} au {fin.strftime("%B %Y")})'
    })

@app.route('/api/cotisations/historique', methods=['GET'])
def get_historique():
    conn = get_db()
    cur = conn.cursor()
    
    if session.get('user_role') == 'membre':
        cur.execute("""
            SELECT 
                c.id, 
                c.montant_total as montant, 
                c.date_paiement, 
                c.enregistre_par,
                c.mois_debut, 
                c.mois_fin, 
                c.nombre_mois,
                'cotisation' as type_paiement, 
                m.nom as membre_nom, 
                m.telephone
            FROM cotisations c
            JOIN membres m ON c.id_membre = m.id
            WHERE c.id_membre = %s
            
            UNION ALL
            
            SELECT 
                ca.id, 
                ca.montant, 
                ca.date_operation as date_paiement, 
                ca.effectue_par as enregistre_par,
                NULL as mois_debut, 
                NULL as mois_fin, 
                NULL as nombre_mois,
                'adhesion' as type_paiement, 
                m.nom as membre_nom, 
                m.telephone
            FROM caisse ca
            JOIN membres m ON ca.source = m.id::text
            WHERE ca.motif LIKE 'Adhésion%' 
            AND m.id = %s
            
            ORDER BY date_paiement DESC
        """, (session['user_id'], session['user_id']))
    else:
        cur.execute("""
            SELECT 
                c.id, 
                c.montant_total as montant, 
                c.date_paiement, 
                c.enregistre_par,
                c.mois_debut, 
                c.mois_fin, 
                c.nombre_mois,
                'cotisation' as type_paiement, 
                m.nom as membre_nom, 
                m.telephone
            FROM cotisations c
            JOIN membres m ON c.id_membre = m.id
            
            UNION ALL
            
            SELECT 
                ca.id, 
                ca.montant, 
                ca.date_operation as date_paiement, 
                ca.effectue_par as enregistre_par,
                NULL as mois_debut, 
                NULL as mois_fin, 
                NULL as nombre_mois,
                'adhesion' as type_paiement, 
                m.nom as membre_nom, 
                m.telephone
            FROM caisse ca
            JOIN membres m ON ca.source = m.id::text
            WHERE ca.motif LIKE 'Adhésion%'
            
            ORDER BY date_paiement DESC LIMIT 100
        """)
    
    historique = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(historique)

# ==================== API CAISSE ====================
@app.route('/api/caisse/solde', methods=['GET'])
def get_solde():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(solde_apres, 0) as solde FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({'solde': solde['solde'] if solde else 0})

@app.route('/api/caisse/operations', methods=['GET'])
def get_operations():
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            id,
            date_operation,
            type,
            montant,
            motif,
            effectue_par,
            source
        FROM caisse 
        ORDER BY date_operation DESC 
        LIMIT 50
    """)
    ops = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for op in ops:
        # Déterminer le type détaillé
        if op['type'] == 'sortie':
            type_detail = 'decaissement'
            type_label = 'Décaissement'
            type_icon = '⬇️'
            type_class = 'bg-danger'
        elif op['type'] == 'entree' and 'Adhésion' in op['motif']:
            type_detail = 'adhesion'
            type_label = 'Adhésion'
            type_icon = '💰'
            type_class = 'bg-info'
        else:
            type_detail = 'cotisation'
            type_label = 'Cotisation'
            type_icon = '📆'
            type_class = 'bg-success'
        
        result.append({
            'id': op['id'],
            'date_operation': op['date_operation'],
            'type': op['type'],
            'type_detail': type_detail,
            'type_label': type_label,
            'type_icon': type_icon,
            'type_class': type_class,
            'montant': op['montant'],
            'motif': op['motif'],
            'effectue_par': op['effectue_par']
        })
    
    return jsonify(result)

# ==================== API STATS ====================
@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) as total FROM membres WHERE role != 'admin'")
    total = cur.fetchone()['total']
    
    cur.execute("SELECT id FROM membres WHERE role != 'admin'")
    membres = cur.fetchall()
    
    a_jour = 0
    for m in membres:
        retard = calculer_mois_retard(m['id'])
        if retard['mois_retard'] == 0:
            a_jour += 1
    
    # Solde total (cotisations + adhésions)
    cur.execute("SELECT COALESCE(SUM(CASE WHEN type = 'entree' THEN montant ELSE -montant END), 0) as solde FROM caisse")
    solde = cur.fetchone()
    
    # Total collecté (cotisations + adhésions)
    cur.execute("SELECT COALESCE(SUM(montant), 0) as total FROM caisse WHERE type = 'entree'")
    total_collecte = cur.fetchone()['total']
    
    cur.close()
    conn.close()
    
    return jsonify({
        'total_membres': total,
        'a_jour': a_jour,
        'en_retard': total - a_jour,
        'total_collecte': total_collecte,
        'solde_caisse': solde['solde'] if solde else 0
    })
@app.route('/api/payer_adhesion', methods=['POST'])
def payer_adhesion():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    data = request.json
    id_membre = data['id_membre']
    montant = data.get('montant', 4000)
    
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer infos membre
    cur.execute("SELECT nom, frais_adhesion_paye, frais_adhesion_reste, adhesion_statut FROM membres WHERE id = %s", (id_membre,))
    membre = cur.fetchone()
    
    if not membre:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Membre non trouvé'})
    
    if membre['adhesion_statut'] == 'payé':
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Adhésion déjà payée'})
    
    reste_actuel = membre['frais_adhesion_reste']
    
    if montant > reste_actuel:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': f'Montant trop élevé. Reste: {reste_actuel} FCFA'})
    
    nouveau_paye = membre['frais_adhesion_paye'] + montant
    nouveau_reste = reste_actuel - montant
    nouveau_statut = 'payé' if nouveau_reste <= 0 else ('partiel' if nouveau_paye > 0 else 'impaye')
    
    # Mettre à jour le membre
    cur.execute("""
        UPDATE membres 
        SET frais_adhesion_paye = %s, frais_adhesion_reste = %s, adhesion_statut = %s
        WHERE id = %s
    """, (nouveau_paye, nouveau_reste, nouveau_statut, id_membre))
    
    # Récupérer le dernier solde de caisse
    cur.execute("SELECT COALESCE(solde_apres, 0) as solde FROM caisse ORDER BY id DESC LIMIT 1")
    solde_row = cur.fetchone()
    solde_actuel = solde_row['solde'] if solde_row else 0
    nouveau_solde = solde_actuel + montant
    
    # Enregistrer l'adhésion dans la caisse
    cur.execute("""
        INSERT INTO caisse (type, montant, motif, source, solde_apres, effectue_par)
        VALUES ('entree', %s, %s, %s, %s, %s)
    """, (montant, f"Adhésion {membre['nom']}", str(id_membre), nouveau_solde, session.get('user_nom', 'admin')))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': f'{montant} FCFA enregistré pour l\'adhésion',
        'adhesion_statut': nouveau_statut,
        'paye': nouveau_paye,
        'reste': nouveau_reste
    })
# ==================== ROUTES MEMBRE ====================
@app.route('/membre')
def membre_dashboard():
    if 'user_id' not in session or session.get('user_role') != 'membre':
        return redirect(url_for('login'))
    return render_template('membre_dashboard.html')

@app.route('/api/membre/dashboard', methods=['GET'])
def api_membre_dashboard():
    if session.get('user_role') != 'membre':
        return jsonify({'error': 'Accès refusé'}), 403
    
    id_membre = session['user_id']
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer infos membre avec adhesion_statut
    cur.execute("""
        SELECT nom, telephone, date_inscription, 
               adhesion_statut, frais_adhesion_paye, frais_adhesion_reste
        FROM membres 
        WHERE id = %s
    """, (id_membre,))
    membre = cur.fetchone()
    
    if not membre:
        cur.close()
        conn.close()
        return jsonify({'error': 'Membre non trouvé'}), 404
    
    # Récupérer le total payé et mois cotisés
    cur.execute("SELECT COALESCE(SUM(montant_total), 0) as total FROM cotisations WHERE id_membre = %s", (id_membre,))
    total_paye = cur.fetchone()['total'] or 0
    
    cur.execute("SELECT COALESCE(SUM(nombre_mois), 0) as mois FROM cotisations WHERE id_membre = %s", (id_membre,))
    mois_cotises = cur.fetchone()['mois'] or 0
    
    # Calculer les mois attendus et le retard
    annee = datetime.now().year
    mois_courant = datetime.now().month
    mois_attendus = mois_courant
    reste_a_payer = max(0, (mois_attendus - mois_cotises) * 1000)
    statut = 'À jour' if reste_a_payer == 0 else 'En retard'
    mois_retard = max(0, mois_attendus - mois_cotises)
    
    # ========== PROCHAIN PAIEMENT INTELLIGENT ==========
    cur.execute("""
        SELECT MAX(mois_fin) as derniere_periode
        FROM cotisations 
        WHERE id_membre = %s
    """, (id_membre,))
    derniere = cur.fetchone()
    
    if derniere and derniere['derniere_periode']:
        derniere_date = derniere['derniere_periode']
        # Prochain mois après la fin de la dernière période
        prochain = derniere_date.replace(day=1)
        if prochain.month == 12:
            prochain = prochain.replace(year=prochain.year + 1, month=1)
        else:
            prochain = prochain.replace(month=prochain.month + 1)
    else:
        # Jamais payé, prochain paiement = janvier de l'année courante
        prochain = datetime(annee, 1, 1)
    
    cur.close()
    conn.close()
    
    # Normaliser l'affichage de l'adhésion
    adhesion_statut = membre['adhesion_statut']
    adhesion_paye = membre['frais_adhesion_paye']
    adhesion_reste = membre['frais_adhesion_reste']
    
    if adhesion_statut == 'payé' or adhesion_reste <= 0:
        adhesion_affichage = 'paye'
    elif adhesion_statut == 'partiel':
        adhesion_affichage = 'partiel'
    else:
        adhesion_affichage = 'impaye'
    
    return jsonify({
        'nom': membre['nom'],
        'telephone': membre['telephone'],
        'date_inscription': membre['date_inscription'].isoformat() if membre['date_inscription'] else None,
        'adhesion_statut': adhesion_affichage,
        'adhesion_paye': adhesion_paye,
        'adhesion_reste': adhesion_reste,
        'statut': statut,
        'mois_retard': mois_retard,
        'mois_cotises': mois_cotises,
        'total_paye': total_paye,
        'reste_a_payer': reste_a_payer,
        'prochain_paiement': prochain.isoformat()
    })

@app.route('/api/membre/historique', methods=['GET'])
def api_membre_historique():
    if session.get('user_role') != 'membre':
        return jsonify({'error': 'Accès refusé'}), 403
    
    id_membre = session['user_id']
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, nombre_mois, montant_total, date_paiement, enregistre_par,
               TO_CHAR(mois_debut, 'Month YYYY') as mois_debut_str,
               TO_CHAR(mois_fin, 'Month YYYY') as mois_fin_str,
               EXTRACT(YEAR FROM mois_debut) as annee
        FROM cotisations 
        WHERE id_membre = %s 
        ORDER BY date_paiement DESC
    """, (id_membre,))
    
    historique = cur.fetchall()
    cur.close()
    conn.close()
    
    result = []
    for h in historique:
        if h['nombre_mois'] == 1:
            periode = h['mois_debut_str'].strip()
        else:
            periode = f"{h['mois_debut_str'].strip()} à {h['mois_fin_str'].strip()} ({h['nombre_mois']} mois)"
        
        result.append({
            'id': h['id'],
            'periode': periode,
            'montant': h['montant_total'],
            'date_paiement': h['date_paiement'].isoformat() if h['date_paiement'] else None,
            'enregistre_par': h['enregistre_par'],
            'paye': True
        })
    
    return jsonify(result)

@app.route('/api/membre/reçu/<int:id>', methods=['GET'])
def api_membre_recu(id):
    if session.get('user_role') != 'membre':
        return jsonify({'error': 'Accès refusé'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.montant_total, c.date_paiement, c.enregistre_par, c.nombre_mois,
               c.mois_debut, c.mois_fin,
               m.nom, m.telephone
        FROM cotisations c
        JOIN membres m ON c.id_membre = m.id
        WHERE c.id = %s AND m.id = %s
    """, (id, session['user_id']))
    recu = cur.fetchone()
    cur.close()
    conn.close()
    
    if recu:
        # Formater la période
        debut = recu['mois_debut'].strftime('%B %Y') if recu['mois_debut'] else '?'
        fin = recu['mois_fin'].strftime('%B %Y') if recu['mois_fin'] else '?'
        
        if recu['nombre_mois'] == 1:
            periode = debut
        else:
            periode = f"{debut} à {fin} ({recu['nombre_mois']} mois)"
        
        return jsonify({
            'success': True,
            'nom': recu['nom'],
            'telephone': recu['telephone'],
            'periode': periode,
            'montant': recu['montant_total'],
            'date': recu['date_paiement'].isoformat() if recu['date_paiement'] else None,
            'enregistre_par': recu['enregistre_par']
        })
    return jsonify({'success': False})

@app.route('/api/membres/<int:id>/date', methods=['PUT'])
def update_membre_date(id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    data = request.json
    nouvelle_date = data.get('date_inscription')
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE membres SET date_inscription = %s WHERE id = %s", (nouvelle_date, id))
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Date mise à jour'})

@app.route('/api/debug/adhesion/<int:id_membre>', methods=['GET'])
def debug_adhesion(id_membre):
    """Diagnostic - version sans vérification de session"""
    # Supprime la vérification temporairement
    # if session.get('user_role') != 'admin':
    #     return jsonify({'error': 'Accès refusé'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    
    # 1. Récupérer les données du membre
    cur.execute("""
        SELECT id, nom, adhesion_statut, frais_adhesion_paye, frais_adhesion_reste, date_inscription
        FROM membres 
        WHERE id = %s
    """, (id_membre,))
    membre = cur.fetchone()
    
    # 2. Récupérer la session actuelle
    session_info = {
        'user_id': session.get('user_id'),
        'user_role': session.get('user_role'),
        'user_nom': session.get('user_nom')
    }
    
    cur.close()
    conn.close()
    
    return jsonify({
        'membre_db': membre,
        'session_info': session_info,
        'message': 'Diagnostic réussi'
    })

# ==================== GESTION DES ADMINS ====================

@app.route('/admins')
def admins_page():
    if session.get('user_role') != 'admin' or not session.get('is_super_admin', False):
        return redirect(url_for('index'))
    return render_template('admins.html')

@app.route('/api/admins', methods=['GET'])
def get_admins():
    if session.get('user_role') != 'admin' or not session.get('is_super_admin', False):
        return jsonify({'error': 'Accès refusé'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, username, is_super_admin FROM admin ORDER BY id")
    admins = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(admins)

@app.route('/api/admins', methods=['POST'])
def add_admin():
    if session.get('user_role') != 'admin' or not session.get('is_super_admin', False):
        return jsonify({'success': False, 'message': 'Accès refusé'}), 403
    
    data = request.json
    username = data.get('username')
    password = data.get('password', 'admin@123')
    nom_complet = data.get('nom_complet', username)
    
    hash_mdp = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("INSERT INTO admin (username, password_hash, nom_complet, is_super_admin) VALUES (%s, %s, %s, FALSE)", 
                    (username, hash_mdp, nom_complet))
        conn.commit()
        return jsonify({'success': True, 'message': f'Admin {username} ajouté'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/api/admins/<int:id>', methods=['DELETE'])
def delete_admin(id):
    if session.get('user_role') != 'admin' or not session.get('is_super_admin', False):
        return jsonify({'success': False, 'message': 'Accès refusé'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    
    # Empêcher de supprimer son propre compte
    cur.execute("SELECT username FROM admin WHERE id = %s", (id,))
    admin = cur.fetchone()
    if admin and admin['username'] == session.get('user_nom'):
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Vous ne pouvez pas supprimer votre propre compte'})
    
    cur.execute("DELETE FROM admin WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ==================== LANCEMENT ====================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)